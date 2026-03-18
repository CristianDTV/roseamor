from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

# Config

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = BASE_DIR / "data" / "roseamor.db"

VALID_CHANNELS = {"ecommerce", "wholesale", "retail", "export"}

# App

app = FastAPI(
    title="RoseAmor Order API",
    description=(
        "API REST para registrar pedidos en RoseAmor.\n\n"
        "Pipeline: formulario web -> validación Pydantic -> SQLite (orders_web)\n\n"
        "Los registros guardados se integran al pipeline ETL en el próximo refresh."
    ),
    version="1.0.0",
    contact={"name": "RoseAmor Data Team"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception Handler

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError) -> JSONResponse:
    """Intercepta y formatea los errores de validación a un español limpio."""
    errores = []
    for err in exc.errors():
        campo = err["loc"][-1] if len(err["loc"]) > 0 else "Desconocido"
        msg = err["msg"]

        # Limpiar prefijo nativo de Pydantic
        if msg.startswith("Value error, "):
            msg = msg.replace("Value error, ", "", 1)
        if msg.startswith("Assertion failed, "):
            msg = msg.replace("Assertion failed, ", "", 1)

        # Traducir errores nativos de Pydantic
        if err["type"] == "missing":
            msg = "Este campo es obligatorio."
        elif err["type"] == "string_too_short":
            msg = "El campo no puede estar vacío."
        elif err["type"] in ("int_parsing", "float_parsing", "int_type", "float_type"):
            msg = "Debe ingresar un número válido."
        elif err["type"] == "greater_than":
            msg = "El valor debe ser mayor a cero."
        elif "date" in err["type"]:
            msg = "Formato de fecha inválido."

        errores.append({"msg": f"[{campo}] {msg}"})

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errores}
    )

# DB Helpers

@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    """Crea la tabla orders_web si no existe."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders_web (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id    TEXT    NOT NULL UNIQUE,
                customer_id TEXT    NOT NULL,
                sku         TEXT    NOT NULL,
                quantity    INTEGER NOT NULL CHECK(quantity > 0),
                unit_price  REAL    NOT NULL CHECK(unit_price > 0),
                order_date  TEXT    NOT NULL,
                channel     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

# Pydantic Schemas

class OrderCreate(BaseModel):
    order_id:    str   = Field(..., min_length=1, max_length=20,  examples=["O002000"])
    customer_id: str   = Field(...,                               examples=["C0001"])
    sku:         str   = Field(...,                               examples=["SKU0001"])
    quantity:    int   = Field(..., gt=0,  le=10_000,             examples=[5])
    unit_price:  float = Field(..., gt=0,  le=100_000,            examples=[29.99])
    order_date:  date  = Field(...,                               examples=["2025-06-01"])
    channel:     str   = Field(...,                               examples=["ecommerce"])

    @field_validator("order_id")
    @classmethod
    def order_id_uppercase(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("customer_id")
    @classmethod
    def validate_customer_id(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.match(r"^C\d{4}$", v):
            raise ValueError("Debe empezar con 'C' seguido de 4 dígitos (ej. C0001).")
        return v

    @field_validator("sku")
    @classmethod
    def validate_sku(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.match(r"^SKU\d{4}$", v):
            raise ValueError("Debe empezar con 'SKU' seguido de 4 dígitos (ej. SKU0001).")
        return v

    @field_validator("channel")
    @classmethod
    def channel_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_CHANNELS:
            raise ValueError(f"Canal no válido. Opciones: {', '.join(sorted(VALID_CHANNELS))}.")
        return v

    @field_validator("order_date", mode="before")
    @classmethod
    def date_not_future(cls, v: object) -> object:
        if isinstance(v, str):
            if not v.strip():
                raise ValueError("La fecha es obligatoria.")
            try:
                parsed = date.fromisoformat(v)
            except ValueError:
                raise ValueError("Formato de fecha inválido. Use YYYY-MM-DD.")
            if parsed > date.today():
                raise ValueError("La fecha del pedido no puede ser futura.")
            return v
        if isinstance(v, date) and v > date.today():
            raise ValueError("La fecha del pedido no puede ser futura.")
        return v

    @model_validator(mode="after")
    def revenue_sanity(self) -> "OrderCreate":
        revenue = self.quantity * self.unit_price
        if revenue > 1_000_000:
            raise ValueError(f"Revenue implícito ({revenue:,.2f}) anómalo. Verifica cantidad y precio.")
        return self

class OrderResponse(BaseModel):
    id:          int
    order_id:    str
    customer_id: str
    sku:         str
    quantity:    int
    unit_price:  float
    revenue:     float
    order_date:  str
    channel:     str
    created_at:  str

class StatusResponse(BaseModel):
    status:  str
    message: str

# Startup

@app.on_event("startup")
def startup() -> None:
    init_db()

# Endpoints

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> str:
    """Sirve un formulario de registro de pedidos minimalista."""
    return """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RoseAmor | Registro de Pedidos</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: #fcfcfc;
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      padding: 1rem;
      color: #333;
    }
    .card {
      background: #fff; border-radius: 4px; padding: 2.5rem;
      box-shadow: 0 4px 12px rgba(0,0,0,0.05); width: 100%; max-width: 520px;
      border-top: 4px solid #368c72;
    }
    h1 { color: #111; font-size: 1.5rem; margin-bottom: .25rem; font-weight: 400; letter-spacing: 0.5px;}
    p.sub { color: #666; font-size:.9rem; margin-bottom:1.5rem; }
    label { display:block; font-size:.85rem; color:#444; margin-bottom:.4rem; font-weight:500; }
    input, select {
      width:100%; padding:.6rem .9rem; border:1px solid #ddd;
      border-radius:2px; font-size:.95rem; margin-bottom:1rem;
      transition: border-color .2s;
    }
    input:focus, select:focus { outline:none; border-color:#368c72; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }
    button {
      width:100%; padding:.85rem; background:#e6c9c4; color:#333;
      border:none; border-radius:2px; font-size:1rem; font-weight:500;
      cursor:pointer; transition: background .2s, color .2s;
      margin-top: 0.5rem;
    }
    button:hover { background:#d4b5b0; color:#111; }
    #msg { margin-top:1.5rem; padding:.75rem 1rem; border-radius:2px;
           display:none; font-size:.9rem; line-height:1.4; }
    #msg.ok  { background:#f0f7f4; color:#368c72; border:1px solid #c2dfd5; }
    #msg.err { background:#fdf0f0; color:#c84b31; border:1px solid #f0caca; }
    .api-link { text-align:center; margin-top:1.5rem; font-size:.85rem; color:#888; }
    .api-link a { color:#368c72; text-decoration:none; font-weight:500; }
    .api-link a:hover { text-decoration:underline; }
  </style>
</head>
<body>
<div class="card">
  <h1>RoseAmor</h1>
  <p class="sub">Registro de pedidos | Panel interno</p>

  <label>Order ID</label>
  <input id="order_id" placeholder="Ej. O002001" />

  <div class="row">
    <div>
      <label>Customer ID</label>
      <input id="customer_id" placeholder="C0001" />
    </div>
    <div>
      <label>SKU</label>
      <input id="sku" placeholder="SKU0001" />
    </div>
  </div>

  <div class="row">
    <div>
      <label>Cantidad</label>
      <input id="quantity" type="number" min="1" placeholder="5" />
    </div>
    <div>
      <label>Precio unitario</label>
      <input id="unit_price" type="number" step="0.01" min="0.01" placeholder="29.99" />
    </div>
  </div>

  <label>Fecha del pedido</label>
  <input id="order_date" type="date" />

  <label>Canal</label>
  <select id="channel">
    <option value="">Seleccionar canal...</option>
    <option value="ecommerce">E-commerce</option>
    <option value="retail">Retail</option>
    <option value="wholesale">Wholesale</option>
    <option value="export">Export</option>
  </select>

  <button onclick="submitOrder()">Registrar Pedido</button>

  <div id="msg"></div>
  <div class="api-link"><a href="/docs" target="_blank">Ver documentación API (Swagger)</a></div>
</div>

<script>
async function submitOrder() {
  const body = {
    order_id:    document.getElementById('order_id').value.trim(),
    customer_id: document.getElementById('customer_id').value.trim(),
    sku:         document.getElementById('sku').value.trim(),
    quantity:    document.getElementById('quantity').value ? parseInt(document.getElementById('quantity').value) : null,
    unit_price:  document.getElementById('unit_price').value ? parseFloat(document.getElementById('unit_price').value) : null,
    order_date:  document.getElementById('order_date').value,
    channel:     document.getElementById('channel').value,
  };
  const msg = document.getElementById('msg');
  msg.style.display = 'none';
  try {
    const res = await fetch('/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (res.ok) {
      msg.className = 'ok'; msg.style.display = 'block';
      msg.textContent = 'Pedido ' + data.order_id + ' registrado correctamente.';
    } else {
      msg.className = 'err'; msg.style.display = 'block';
      
      // Formatear los errores limpiamente separados por saltos de línea
      if (Array.isArray(data.detail)) {
        msg.innerHTML = '<strong>Corrija los siguientes errores:</strong><br/>' + 
                        data.detail.map(e => '- ' + e.msg).join('<br/>');
      } else {
        msg.textContent = 'Error: ' + data.detail;
      }
    }
  } catch (e) {
    msg.className = 'err'; msg.style.display = 'block';
    msg.textContent = 'Error de red: ' + e.message;
  }
}
</script>
</body>
</html>
"""

@app.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un nuevo pedido",
    tags=["Orders"],
)
def create_order(order: OrderCreate) -> OrderResponse:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM orders_web WHERE order_id = ?", (order.order_id,)
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El order_id '{order.order_id}' ya existe.",
            )

        conn.execute(
            """
            INSERT INTO orders_web
                (order_id, customer_id, sku, quantity, unit_price, order_date, channel)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.customer_id,
                order.sku,
                order.quantity,
                order.unit_price,
                order.order_date.isoformat(),
                order.channel,
            ),
        )
        row = conn.execute(
            "SELECT * FROM orders_web WHERE order_id = ?", (order.order_id,)
        ).fetchone()

    return OrderResponse(
        id=row["id"],
        order_id=row["order_id"],
        customer_id=row["customer_id"],
        sku=row["sku"],
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        revenue=round(row["quantity"] * row["unit_price"], 2),
        order_date=row["order_date"],
        channel=row["channel"],
        created_at=row["created_at"],
    )

@app.get(
    "/orders",
    response_model=list[OrderResponse],
    summary="Listar pedidos registrados",
    tags=["Orders"],
)
def list_orders(
    limit:   int = Query(50, ge=1,  le=500),
    offset:  int = Query(0,  ge=0),
    channel: Optional[str] = Query(None),
) -> list[OrderResponse]:
    with get_db() as conn:
        if channel:
            rows = conn.execute(
                "SELECT * FROM orders_web WHERE channel=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (channel, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders_web ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    return [
        OrderResponse(
            id=r["id"],
            order_id=r["order_id"],
            customer_id=r["customer_id"],
            sku=r["sku"],
            quantity=r["quantity"],
            unit_price=r["unit_price"],
            revenue=round(r["quantity"] * r["unit_price"], 2),
            order_date=r["order_date"],
            channel=r["channel"],
            created_at=r["created_at"],
        )
        for r in rows
    ]

@app.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Obtener un pedido por ID",
    tags=["Orders"],
)
def get_order(order_id: str) -> OrderResponse:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM orders_web WHERE order_id = ?", (order_id.upper(),)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Pedido '{order_id}' no encontrado.")
    return OrderResponse(
        id=row["id"],
        order_id=row["order_id"],
        customer_id=row["customer_id"],
        sku=row["sku"],
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        revenue=round(row["quantity"] * row["unit_price"], 2),
        order_date=row["order_date"],
        channel=row["channel"],
        created_at=row["created_at"],
    )

@app.delete(
    "/orders/{order_id}",
    response_model=StatusResponse,
    summary="Eliminar un pedido",
    tags=["Orders"],
)
def delete_order(order_id: str) -> StatusResponse:
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM orders_web WHERE order_id = ?", (order_id.upper(),)
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Pedido '{order_id}' no encontrado.")
    return StatusResponse(status="ok", message=f"Pedido '{order_id}' eliminado.")

@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}