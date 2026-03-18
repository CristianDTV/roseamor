# RoseAmor — Full Stack Data Solution

> **Prueba técnica · Ingeniero/a Full Stack de Datos (SQL + BI + Web)**
> Stack: Python 3.10+ · Pandas · SQLite · FastAPI · Power BI

---

## Índice

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Arquitectura y flujo de datos](#2-arquitectura-y-flujo-de-datos)
3. [Limpieza de datos — hallazgos y decisiones](#3-limpieza-de-datos)
4. [Modelo de datos (Star Schema)](#4-modelo-de-datos)
5. [Tablas de la base de datos](#5-tablas-de-la-base-de-datos)
6. [KPIs destacados](#6-kpis-destacados)
7. [Dashboard Power BI](#7-dashboard-power-bi)
8. [Cómo ejecutar](#8-cómo-ejecutar)
9. [App web — Registro de pedidos](#9-app-web)
10. [Cómo actualizar — Refresh con nuevos CSVs](#10-cómo-actualizar)
11. [Estructura del repositorio](#11-estructura-del-repositorio)
12. [Decisiones técnicas](#12-decisiones-técnicas)

---

## 1. Resumen ejecutivo

Este proyecto transforma tres archivos CSV crudos de RoseAmor en una base de datos analítica confiable con arquitectura de **cuatro capas** (raw → staging → validación semántica → marts), 11 consultas SQL avanzadas con window functions y CTEs, un dashboard interactivo en Power BI, y una API REST con documentación Swagger para registrar nuevos pedidos.

| Métrica | Valor |
|---|---|
| Registros originales (orders) | 1 515 |
| Registros técnicamente inválidos descartados | 31 |
| Registros en cuarentena semántica | 33 |
| Registros en `fact_sales` (ventas limpias) | 1 476 |
| Ingresos totales | $1 545 647,75 |
| Margen bruto total | $1 028 567,26 (66,5 %) |
| Pedidos únicos | 1 476 |
| Ticket promedio | $1 047,19 |

---

## 2. Arquitectura y flujo de datos

```
┌──────────────────────────────────────────────────────────────────────┐
│                         FUENTES DE DATOS                             │
│   data/raw/customers.csv    orders.csv    products.csv               │
└───────────────────────┬──────────────────────────────────────────────┘
                        │  etl/pipeline.py
                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  CAPA 1 — RAW  (audit trail, nunca modificar)                        │
│  raw_customers  |  raw_orders  |  raw_products                       │
│                                                                      │
│  • Carga verbatim con dtype=str (sin casteo)                         │
│  • Agrega: _source_file, _loaded_at, _row_hash (MD5)                │
└───────────────────────┬──────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  CAPA 2 — STAGING  (limpieza técnica y tipado)                       │
│  stg_customers  |  stg_orders  |  stg_products                      │
│                                                                      │
│  • Deduplicación por PK                                              │
│  • Nulos → imputación o exclusión (según impacto en métricas)        │
│  • Negativos → conversión o flag                                     │
│  • Fechas inválidas → exclusión                                      │
│  • Type casting, normalización de texto, columnas derivadas          │
└───────────────────────┬──────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  CAPA 2b — VALIDACIÓN SEMÁNTICA (quarantine)                         │
│  stg_orders enriquecido  |  orders_quarantine                        │
│                                                                      │
│  • is_pre_signup    → order_date < customer created_at               │
│  • is_inactive_sale → producto con active = False                    │
│  • is_below_cost    → unit_price < costo del producto                │
│  • Filas críticas aisladas en tabla orders_quarantine                │
└───────────────────────┬──────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  CAPA 3 — MARTS  (star schema para BI)                               │
│                                                                      │
│  Dimensiones:  dim_customer · dim_product · dim_date                 │
│  Hechos:       fact_sales                                            │
│  Marts:        mart_monthly_summary · mart_customer_lifetime         │
│                mart_product_performance                              │
└──────────┬────────────────────────────┬─────────────────────────────┘
           │                            │
           ▼                            ▼
   sql/kpis.sql                  Power BI Desktop
   (11 queries analíticas)       (Dashboard + DAX)
           │
           ▼
   app/main.py  →  orders_web  →  siguiente refresh
   (FastAPI REST)
```

**Base de datos:** `data/roseamor.db` (SQLite — portable, sin servidor, ideal para evaluación)
**Orquestación:** script único `etl/pipeline.py` — completamente idempotente

---

## 3. Limpieza de datos

El proceso se divide en dos capas distintas: la **Capa 2** resuelve problemas técnicos (tipos, formatos, nulos estructurales) y la **Capa 2b** resuelve problemas semánticos (reglas de negocio, inconsistencias entre tablas).

### 3.1 Problemas técnicos — Capa 2 (Staging)

#### `customers.csv` — 200 registros originales

| Prioridad | Campo | Problema | Cantidad | Decisión | Justificación |
|---|---|---|---|---|---|
| Media | `country` | Nulo | 5 | Imputar `"Unknown"` | El cliente existe; el país puede corregirse desde MDM |
| Media | `segment` | Nulo | 5 | Imputar `"Unknown"` | Idem; valor de control claro y filtrable en BI |
| Media | `country`, `segment`, `name` | Texto sin normalizar | — | `.strip().str.title()` | Evita duplicados por capitalización ("ECUADOR" vs "Ecuador") |

**Resultado:** 200 filas limpias, 0 descartadas.

#### `products.csv` — 60 registros originales

| Prioridad | Campo | Problema | Cantidad | Decisión | Justificación |
|---|---|---|---|---|---|
| Media | `category` | Nulo | 2 | Imputar `"Uncategorized"` | El producto existe; categoría completable desde catálogo maestro |
| Alta | `cost` | Negativo (ej. −6,94) | 3 | Convertir a valor absoluto | Error de signo en captura; el valor absoluto es el costo real |

> **Nota sobre costos negativos:** en producción lo correcto sería levantar un ticket al equipo de MDM y bloquear esos SKUs de métricas financieras hasta confirmar. En este contexto se asume error de signo y se aplica `abs()` para no perder los registros.

**Resultado:** 60 filas limpias, 0 descartadas.

#### `orders.csv` — 1 515 registros originales

| Prioridad | Campo | Problema | Cantidad | Decisión | Justificación |
|---|---|---|---|---|---|
| Crítica | `order_id` | 15 duplicados exactos de fila | 15 | Conservar primera aparición, descartar resto | Un pedido no puede existir dos veces; posible re-envío del CSV |
| Crítica | `order_date` | Fechas inválidas (ej. 2025-13-40) | 6 | **Excluir** | Revenue no puede atribuirse a un período inexistente |
| Crítica | `unit_price` | Nulo | 10 | **Excluir** | Sin precio no hay revenue — métrica crítica imposible de calcular |
| Alta | `quantity` | Negativa (devoluciones) | 8 | Flag `is_return = True`, conservar | Las devoluciones tienen valor analítico; se excluyen de `fact_sales` pero quedan en staging para auditoría |
| Baja | `channel` | Capitalización inconsistente | — | `.strip().str.lower()` | Homologar para joins y filtros en BI |

**Total descartados técnicamente:** 31 filas (15 dupes + 6 fechas + 10 precios nulos)
**Resultado staging:** 1 484 filas

### 3.2 Problemas semánticos — Capa 2b (Validación / Quarantine)

Estos problemas requieren cruzar información entre tablas o aplicar reglas de negocio. No son detectables mirando una sola tabla.

| Prioridad | Tablas | Hallazgo | Cantidad | Decisión | Justificación |
|---|---|---|---|---|---|
| Crítica | `orders` × `customers` | `order_date < created_at` (pedido antes del alta del cliente) | 33 | Flag `is_pre_signup = True` + fila en `orders_quarantine` | Puede indicar error en `created_at`, migración de datos históricos o fraude. Se aisla para revisión del negocio; no se elimina porque el pedido puede ser real. Se excluye de análisis de cohortes. |
| Monitoreo | `orders` × `products` | Ventas sobre productos inactivos (`active = False`) | 271 | Flag `is_inactive_sale = True` | No se rechaza: pedidos históricos sobre productos discontinuados son válidos. El flag permite filtrarlos en BI y es la base para implementar SCD Tipo 2 en el futuro. |
| Monitoreo | `orders` × `products` | `unit_price < cost` (venta por debajo del costo) | 137 | Flag `is_below_cost = True` | Pueden ser liquidaciones, promociones o errores. Se retienen para auditoría financiera. No se modifican los precios porque alterar datos transaccionales históricos es una mala práctica. |
| Media | `customers` × `orders` | `segment` ("E-commerce") vs `channel` ("ecommerce") — nomenclatura inconsistente | — | `channel` normalizado con `.lower()`; `segment` con `.title()` | Representan dimensiones distintas (segmento de cliente vs canal de venta). No se homologan entre sí. |

**Tabla `orders_quarantine`:** contiene las 33 filas pre-signup con columnas adicionales `_quarantine_reason` y `_quarantined_at` para trazabilidad completa.

### 3.3 Resumen consolidado de impacto

```
orders originales              1 515
─ duplicados exactos             -15
─ fechas inválidas                -6
─ unit_price nulo                -10
                               ─────
stg_orders (staging)           1 484
─ devoluciones (is_return)        -8
                               ─────
fact_sales (ventas)            1 476   ← base de todos los KPIs

Flags analíticos en fact_sales:
  is_pre_signup        33 filas  (2,2 %)
  is_inactive_sale    271 filas  (18,4 %)
  is_below_cost       137 filas  (9,3 %)

orders_quarantine       33 filas  (aisladas para revisión del negocio)
```

> **Principio aplicado:** nunca se alteran ni eliminan datos de la capa raw. Los datos cuestionables se marcan, aíslan o excluyen en capas superiores, siempre con trazabilidad del motivo. El negocio puede revisar y revertir cualquier decisión sin perder el dato original.

---

## 4. Modelo de datos

Arquitectura **star schema** optimizada para Power BI: una tabla de hechos central rodeada de dimensiones limpias y desnormalizadas.

```
                    ┌─────────────────────┐
                    │      dim_date        │
                    │─────────────────────│
                    │ date_id    (PK)      │
                    │ date                 │
                    │ year / quarter       │
                    │ month / month_name   │
                    │ week / day_of_week   │
                    │ is_weekend           │
                    └──────────┬──────────┘
                               │
┌─────────────────────┐        │        ┌─────────────────────┐
│    dim_customer      │        │        │    dim_product       │
│─────────────────────│        │        │─────────────────────│
│ customer_id  (PK)   │        │        │ sku        (PK)     │
│ name                │        │        │ category            │
│ country             │        │        │ cost                │
│ segment             │◄───────┼───────►│ active              │
│ created_at          │        │        └─────────────────────┘
└─────────────────────┘        │
                               ▼
                    ┌────────────────────────────────────┐
                    │            fact_sales               │
                    │────────────────────────────────────│
                    │ order_id        (PK)                │
                    │ customer_id     (FK → dim_customer) │
                    │ sku             (FK → dim_product)  │
                    │ date_id         (FK → dim_date)     │
                    │ channel                             │
                    │ quantity                            │
                    │ unit_price                          │
                    │ revenue         = qty × price       │
                    │ cost_total      = qty × cost        │
                    │ gross_margin    = revenue − cost    │
                    │ margin_pct                          │
                    │ is_pre_signup   (flag analítico)    │
                    │ is_inactive_sale (flag analítico)   │
                    │ is_below_cost   (flag analítico)    │
                    └────────────────────────────────────┘
```

### Marts pre-agregados (para velocidad en BI)

| Tabla | Descripción |
|---|---|
| `mart_monthly_summary` | Revenue, margen, pedidos y unidades por mes y canal |
| `mart_customer_lifetime` | CLV, órdenes, ticket promedio, primera/última compra por cliente |
| `mart_product_performance` | Unidades, revenue y margen por SKU |

---

## 5. Tablas de la base de datos

Lista completa de tablas en `roseamor.db`:

| Tabla | Capa | Descripción |
|---|---|---|
| `raw_customers` | Raw | customers.csv tal cual se recibió |
| `raw_orders` | Raw | orders.csv tal cual se recibió |
| `raw_products` | Raw | products.csv tal cual se recibió |
| `stg_customers` | Staging | Clientes limpios y tipados |
| `stg_orders` | Staging + flags | Pedidos limpios con flags semánticos |
| `stg_products` | Staging | Productos limpios |
| `orders_quarantine` | Cuarentena | Pedidos con `order_date < created_at` |
| `dim_customer` | Mart | Dimensión cliente |
| `dim_product` | Mart | Dimensión producto |
| `dim_date` | Mart | Dimensión fecha (762 días) |
| `fact_sales` | Mart | Hechos de ventas (1 476 filas) |
| `mart_monthly_summary` | Mart | Revenue por mes y canal |
| `mart_customer_lifetime` | Mart | Valor de vida del cliente |
| `mart_product_performance` | Mart | Rendimiento por SKU |
| `orders_web` | App | Pedidos registrados vía API web |

---

## 6. KPIs destacados

| KPI | Valor |
|---|---|
|Ingresos totales | **$1 545 647,75** |
|Margen bruto | **$1 028 567,26** |
|Margen bruto % | **66,5 %** |
|Pedidos únicos | **1 476** |
|Ticket promedio | **$1 047,19** |

**Canal líder:** E-commerce — $545 779 (35 % del revenue)
**Categoría más rentable:** Premium — 74,1 % de margen
**Top cliente:** Diego Pérez (Ecuador) — $18 697

**Alertas de calidad pendientes de revisión con el negocio:**
- 33 pedidos pre-signup → posible error en fecha de alta de cliente o migración histórica
- 271 ventas sobre productos inactivos → considerar SCD Tipo 2
- 137 ventas por debajo del costo → auditoría financiera recomendada

---

## 7. Dashboard Power BI

### Instrucciones de conexión

1. Abrir **Power BI Desktop**
2. `Inicio` → `Obtener datos` → `Más...` → buscar **SQLite**
3. Seleccionar `data/roseamor.db`
4. Importar: `fact_sales`, `dim_customer`, `dim_product`, `dim_date`
5. Verificar relaciones en la vista **Modelo**:

| Desde | Hacia | Tipo |
|---|---|---|
| `fact_sales[customer_id]` | `dim_customer[customer_id]` | Muchos a uno |
| `fact_sales[sku]` | `dim_product[sku]` | Muchos a uno |
| `fact_sales[date_id]` | `dim_date[date_id]` | Muchos a uno |

### Medidas DAX recomendadas

```dax
-- KPIs base
Total Revenue    = SUM(fact_sales[revenue])
Total Margin     = SUM(fact_sales[gross_margin])
Margin %         = DIVIDE([Total Margin], [Total Revenue])
Avg Ticket       = DIVIDE([Total Revenue], DISTINCTCOUNT(fact_sales[order_id]))
Total Orders     = DISTINCTCOUNT(fact_sales[order_id])

-- Revenue limpio (excluye ventas bajo costo y pre-signup)
Revenue Clean    = CALCULATE([Total Revenue],
                    fact_sales[is_below_cost] = FALSE,
                    fact_sales[is_pre_signup] = FALSE)

-- Alerta: % ventas por debajo del costo
Below Cost %     = DIVIDE(
                    COUNTROWS(FILTER(fact_sales, fact_sales[is_below_cost] = TRUE)),
                    [Total Orders])

-- Crecimiento mensual
Revenue MoM %    = VAR cur = [Total Revenue]
                   VAR prv = CALCULATE([Total Revenue],
                               DATEADD(dim_date[date], -1, MONTH))
                   RETURN DIVIDE(cur - prv, prv)
```

### Estructura del dashboard — 3 páginas

**Página 1 — Executive Summary**
- 5 tarjetas KPI: Revenue, Margin, Margin %, Orders, Avg Ticket
- Ventas por mes (barras + línea de tendencia MoM)
- Ventas por canal (dona)
- Filtros: rango de fecha, canal, categoría, país

**Página 2 — Clientes & Segmentos**
- Top 10 clientes por ingresos (barra horizontal)
- Revenue por segmento de cliente
- Mapa de revenue por país
- Filtros: segmento, país

**Página 3 — Productos & Calidad**
- Top 10 productos por revenue (barra horizontal)
- Margen por categoría (cascada o barras agrupadas)
- Tabla de alertas: pedidos `is_below_cost = TRUE` para auditoría
- Filtros: categoría, estado activo/inactivo

> El archivo `RoseAmor_Dashboard.pbix` está en la raíz del repositorio.

---

## 8. Cómo ejecutar

### Requisitos previos

```
Python >= 3.10
pip
Power BI Desktop (para el dashboard)
```

### Paso 1 — Clonar el repositorio

```bash
git clone https://github.com/tu-usuario/roseamor-data.git
cd roseamor-data
```

### Paso 2 — Instalar dependencias

```bash
pip install -r requirements.txt
# o con make:
make install
```

### Paso 3 — Verificar archivos fuente

Confirmar que los tres CSV originales estén en:

```
data/raw/customers.csv
data/raw/orders.csv
data/raw/products.csv
```

### Paso 4 — Ejecutar el pipeline ETL

```bash
python3 etl/pipeline.py
# o con make:
make pipeline
```

Salida esperada (resumen):

```
[INFO] ── LAYER 1: Loading raw CSVs ──
[INFO]   raw_customers: 200 rows  |  raw_orders: 1,515 rows  |  raw_products: 60 rows
[INFO] ── LAYER 2: Staging customers ──
[WARNING]   5 null country → filled with 'Unknown'
[WARNING]   5 null segment → filled with 'Unknown'
[INFO] ── LAYER 2: Staging products ──
[WARNING]   2 null category → filled with 'Uncategorized'
[WARNING]   3 negative cost → converted to absolute value
[INFO] ── LAYER 2: Staging orders ──
[WARNING]   Removed 15 duplicate order_id rows
[WARNING]   6 invalid order_date → rows excluded
[WARNING]   8 negative quantity → flagged as is_return=True
[WARNING]   10 null unit_price → rows excluded
[INFO] ── LAYER 2b: Semantic validation (quarantine) ──
[WARNING]   33 orders where order_date < customer created_at → quarantine
[WARNING]   271 orders on inactive products → flagged is_inactive_sale=True
[WARNING]   137 orders where unit_price < cost → flagged is_below_cost=True
[INFO] ── LAYER 3: Building marts ──
[INFO]   fact_sales: 1,476 rows
[INFO] Pipeline finished successfully ✓
```

Esto genera `data/roseamor.db` con todas las capas y tablas.

### Paso 5 — Iniciar la API web

```bash
uvicorn app.main:app --reload --port 8000
# o con make:
make api
```

- **Formulario web:** http://localhost:8000
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

### Paso 6 — Conectar Power BI

Ver sección [7. Dashboard Power BI](#7-dashboard-power-bi).

### Comandos Make disponibles

```bash
make install    # instala dependencias
make pipeline   # corre el ETL completo
make api        # arranca FastAPI en puerto 8000
make all        # pipeline + api de un solo golpe
make clean      # borra roseamor.db (reset total)
make test       # smoke test con curl (requiere API corriendo)
```

---

## 9. App web

API REST para registrar pedidos, construida con **FastAPI + SQLite + Pydantic v2**.

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Formulario HTML de registro |
| `POST` | `/orders` | Crear pedido nuevo (JSON) |
| `GET` | `/orders` | Listar pedidos (paginación + filtro canal) |
| `GET` | `/orders/{order_id}` | Obtener pedido por ID |
| `DELETE` | `/orders/{order_id}` | Eliminar pedido |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI interactivo |

### Validaciones implementadas (Pydantic v2)

| Campo | Validación |
|---|---|
| `order_id` | Requerido, único en BD, máx. 20 caracteres, convertido a mayúsculas |
| `customer_id` | Formato `C####` (ej. C0001) — regex `^C\d{4}$` |
| `sku` | Formato `SKU####` (ej. SKU0001) — regex `^SKU\d{4}$` |
| `quantity` | Entero positivo, máx. 10 000 |
| `unit_price` | Decimal positivo |
| `order_date` | Formato `YYYY-MM-DD`, no puede ser fecha futura |
| `channel` | Enum: `ecommerce \| wholesale \| retail \| export` |
| Revenue implícito | Alerta si `quantity × unit_price > 1 000 000` (anomaly guard) |

### Ejemplo de llamada

```bash
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "order_id":    "O002001",
    "customer_id": "C0001",
    "sku":         "SKU0001",
    "quantity":    5,
    "unit_price":  29.99,
    "order_date":  "2025-06-01",
    "channel":     "ecommerce"
  }'
```

### Dónde queda la data

Los pedidos se guardan en la tabla `orders_web` dentro de `data/roseamor.db`:

```sql
SELECT * FROM orders_web ORDER BY id DESC LIMIT 10;
```

En el siguiente refresh del pipeline estos registros pueden incorporarse al flujo histórico exportando `orders_web` a CSV y unificándolo con `data/raw/orders.csv`.

---

## 10. Cómo actualizar

### Escenario A — CSV de reemplazo total (el más común)

```bash
# 1. Reemplazar archivo(s) en data/raw/
cp nuevo_orders.csv data/raw/orders.csv

# 2. Re-ejecutar el pipeline (idempotente)
make pipeline

# 3. En Power BI Desktop: Inicio → Actualizar
```

El pipeline es **completamente idempotente**: cada ejecución reconstruye todas las tablas desde cero. No hay estado acumulado que pueda corromperse.

### Escenario B — CSV incremental (solo registros nuevos)

Si el archivo nuevo contiene únicamente las filas nuevas:

1. En `etl/pipeline.py`, cambiar `if_exists="replace"` por `if_exists="append"` en las tablas de staging.
2. Agregar chequeo de duplicados por `_row_hash` antes de insertar:

```python
existing = pd.read_sql("SELECT _row_hash FROM raw_orders", conn)["_row_hash"]
df = df[~df["_row_hash"].isin(existing)]
```

### Escenario C — Pedidos nuevos vía API web

```bash
# Exportar pedidos registrados vía web
sqlite3 data/roseamor.db \
  ".headers on" ".mode csv" \
  "SELECT order_id,customer_id,sku,quantity,unit_price,order_date,channel FROM orders_web;" \
  > data/raw/orders_web_batch.csv

# Unificar con histórico y re-ejecutar
cat data/raw/orders.csv data/raw/orders_web_batch.csv > /tmp/orders_merged.csv
cp /tmp/orders_merged.csv data/raw/orders.csv
make pipeline
```

### Automatización (producción)

```bash
# cron job: pipeline diario a las 2 am
0 2 * * * cd /ruta/roseamor && python3 etl/pipeline.py >> logs/pipeline_$(date +\%Y\%m\%d).log 2>&1
```

---

## 11. Estructura del repositorio

```
roseamor/
│
├── data/
│   ├── raw/                        # CSVs originales — NO modificar manualmente
│   │   ├── customers.csv
│   │   ├── orders.csv
│   │   └── products.csv
│   └── roseamor.db                 # SQLite generado por el pipeline
│
├── etl/
│   └── pipeline.py                 # Pipeline 4 capas: raw → staging → quarantine → marts
│
├── sql/
│   └── kpis.sql                    # 11 queries analíticas (CTEs + window functions)
│
├── app/
│   └── main.py                     # FastAPI: formulario web + 5 endpoints REST
│
├── RoseAmor_Dashboard.pbix         # Dashboard Power BI
├── requirements.txt                # Dependencias Python
├── Makefile                        # make pipeline | make api | make all | make clean
└── README.md                       # Este archivo
```

> `data/roseamor.db` no debería commitearse en producción (añadir a `.gitignore`). Se incluye aquí para la evaluación, además el evaluador pueda abrir el dashboard directamente sin ejecutar el pipeline.

---

## 12. Decisiones técnicas

| Decisión | Alternativa considerada | Por qué esta |
|---|---|---|
| **SQLite** como base de datos | PostgreSQL | Portabilidad total: corre sin servidor en cualquier PC. En producción solo cambia la connection string. |
| **Pandas** para ETL | dbt / Spark / DuckDB | Suficiente para el volumen (1 500 filas); sin infraestructura adicional. DuckDB habría sido más elegante para SQL puro, pero requería instalación extra sin red. |
| **4 capas** (raw / staging / quarantine / marts) | Cargar CSV directo al BI | Trazabilidad completa, separación de responsabilidades y posibilidad de auditar cada transformación. La capa raw nunca se modifica. |
| **`orders_quarantine`** como tabla separada | Descartar los 33 pre-signup | Datos cuestionables se aíslan, no se borran. El negocio puede revisarlos y reintegrarlos. Eliminar datos históricos es irrecuperable. |
| **Flags analíticos** en `fact_sales` | Rechazar o corregir directamente | Estos casos no son necesariamente errores: pueden ser liquidaciones, promos o datos históricos válidos. El flag permite que el analista decida qué incluir en cada reporte. |
| **Exclusión** de filas sin precio/fecha | Imputar | Mejor excluir y documentar que imputar datos críticos para revenue. Imputar un precio de venta introduce sesgo en KPIs financieros. |
| **FastAPI** | Flask / Django | Swagger automático en `/docs`, validaciones declarativas con Pydantic v2, tipado fuerte, asíncrono. Más productivo para una API REST moderna. |
| **Star schema** | Tabla plana desnormalizada | Rendimiento óptimo en Power BI, queries más claras y predecibles, fácil mantenimiento cuando cambian las dimensiones. |
| **Pipeline idempotente** | Inserciones incrementales | Más simple de razonar y de depurar: el mismo input siempre produce el mismo output. Sin estado acumulado que pueda corromperse entre ejecuciones. |

---

*Desarrollado por Cristian Trávez*
