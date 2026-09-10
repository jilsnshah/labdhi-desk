-- Labdhi Desk - the book, relationally.
--
-- Written in SQLite's dialect; db.schema_for_pg() adapts it for Postgres.
--
-- Two kinds of table, and the rule between them:
--
--   MASTER DATA  states, parties, warehouses, materials, grades, manufacturers,
--                products. Each thing exists once, has an id, and is edited in
--                one place (Setup, or an "Add new" form inside a ticket).
--
--   TRANSACTIONS deals, lots, allocations, stock_moves. These only ever point
--                at master data by id. A deal never carries a party's name, a
--                warehouse's name or a material's name as text - rename any of
--                them and every deal, lot and report follows at once.
--
-- Quantities are integer grams, money is integer paise, rates are paise per kg.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ================================================================ master data

-- GST state codes: the first two digits of every GSTIN. Seeded, read-only.
CREATE TABLE IF NOT EXISTS states (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

-- A counterparty. There is no buyer/seller split: the same firm is on either
-- side of a deal from one week to the next, and a transporter is a party too.
-- Identity is the GSTIN when there is one (two branches of one firm are two
-- GSTINs, so two parties); otherwise the name.
CREATE TABLE IF NOT EXISTS parties (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  slug        TEXT NOT NULL UNIQUE,
  gstin       TEXT UNIQUE,
  pan         TEXT,                     -- read off the GSTIN when there is one
  state_code  TEXT REFERENCES states(code),
  phone       TEXT,
  address     TEXT,
  notes       TEXT,
  created_at  TEXT NOT NULL
);

-- Where stock physically sits.
CREATE TABLE IF NOT EXISTS warehouses (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  address     TEXT,
  created_at  TEXT NOT NULL
);

-- The product tree: material -> grade -> (with a manufacturer) product.
-- A manufacturer is who made the resin, never the party you trade with, and
-- one manufacturer makes grades of many materials.
CREATE TABLE IF NOT EXISTS materials (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grades (
  id          INTEGER PRIMARY KEY,
  material_id INTEGER NOT NULL REFERENCES materials(id),
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE (material_id, name)
);

CREATE TABLE IF NOT EXISTS manufacturers (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL
);

-- The stock item master. Inventory is kept per product and per warehouse:
-- PVC S65 from Reliance and PVC S65 from DCW are two products, never pooled.
CREATE TABLE IF NOT EXISTS products (
  id              INTEGER PRIMARY KEY,
  grade_id        INTEGER NOT NULL REFERENCES grades(id),
  manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id),
  packing         TEXT,
  created_at      TEXT NOT NULL,
  UNIQUE (grade_id, manufacturer_id)
);

-- ================================================================ transactions

-- A sauda. BUY: stock received into warehouse_id. SELL: stock dispatched
-- from warehouse_id, drawn only from lots sitting there.
CREATE TABLE IF NOT EXISTS deals (
  id             INTEGER PRIMARY KEY,
  ref            TEXT NOT NULL UNIQUE,          -- Sauda No., LE/26-27/0001
  side           TEXT NOT NULL CHECK (side IN ('buy','sell')),
  status         TEXT NOT NULL CHECK (status IN ('draft','booked','cancelled')),
  party_id       INTEGER NOT NULL REFERENCES parties(id),
  product_id     INTEGER NOT NULL REFERENCES products(id),
  warehouse_id   INTEGER REFERENCES warehouses(id),
  qty_g          INTEGER NOT NULL CHECK (qty_g > 0),
  rate_paise     INTEGER NOT NULL CHECK (rate_paise >= 0),
  plus_gst       INTEGER NOT NULL DEFAULT 1,
  deal_date      TEXT NOT NULL,
  payment_due    TEXT,
  ex_place       TEXT,                          -- pricing basis, recorded as typed
  transporter_id INTEGER REFERENCES parties(id),
  freight_by     TEXT,
  delivery_by    TEXT,
  payment_terms  TEXT,
  eway           TEXT,
  remarks        TEXT,
  alloc_policy   TEXT,
  uncovered_g    INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  booked_at      TEXT,
  cancelled_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_deals_product   ON deals(product_id, status);
CREATE INDEX IF NOT EXISTS ix_deals_party     ON deals(party_id, status);
CREATE INDEX IF NOT EXISTS ix_deals_warehouse ON deals(warehouse_id, status);
CREATE INDEX IF NOT EXISTS ix_deals_date      ON deals(deal_date DESC, id DESC);

-- The stock record: some quantity of one product, from one purchase, at one
-- cost, sitting in one warehouse. A booked BUY creates the first lot. A
-- transfer moves grams into a new lot in the other warehouse (parent_lot_id
-- points back), so what was sold from where stays true forever.
--
--   available = qty_g - qty_allocated_g - qty_out_g
--   qty_allocated_g  sold, through allocations
--   qty_out_g        moved to another warehouse, or written off
CREATE TABLE IF NOT EXISTS lots (
  id              INTEGER PRIMARY KEY,
  label           TEXT NOT NULL,
  deal_id         INTEGER NOT NULL REFERENCES deals(id),
  product_id      INTEGER NOT NULL REFERENCES products(id),
  warehouse_id    INTEGER NOT NULL REFERENCES warehouses(id),
  supplier_id     INTEGER NOT NULL REFERENCES parties(id),
  parent_lot_id   INTEGER REFERENCES lots(id),
  rate_paise      INTEGER NOT NULL,
  qty_g           INTEGER NOT NULL CHECK (qty_g > 0),
  qty_allocated_g INTEGER NOT NULL DEFAULT 0 CHECK (qty_allocated_g >= 0),
  qty_out_g       INTEGER NOT NULL DEFAULT 0 CHECK (qty_out_g >= 0),
  status          TEXT NOT NULL CHECK (status IN ('open','exhausted','cancelled')),
  booked_at       TEXT NOT NULL,
  CHECK (qty_allocated_g + qty_out_g <= qty_g)
);
CREATE INDEX IF NOT EXISTS ix_lots_stock ON lots(product_id, warehouse_id, status);
CREATE INDEX IF NOT EXISTS ix_lots_deal  ON lots(deal_id);

-- Which purchased grams a sale consumed, at what cost. Soft-deleted
-- (active = 0) when a sale is cancelled or re-planned, so history survives.
CREATE TABLE IF NOT EXISTS allocations (
  id              INTEGER PRIMARY KEY,
  sale_deal_id    INTEGER NOT NULL REFERENCES deals(id),
  lot_id          INTEGER NOT NULL REFERENCES lots(id),
  qty_g           INTEGER NOT NULL CHECK (qty_g > 0),
  cost_paise      INTEGER NOT NULL,
  sale_rate_paise INTEGER NOT NULL,
  method          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  active          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_alloc_sale ON allocations(sale_deal_id, active);
CREATE INDEX IF NOT EXISTS ix_alloc_lot  ON allocations(lot_id, active);

-- Every movement of stock that is not a purchase or a sale.
--   transfer  qty_g > 0 leaves lot_id and arrives in to_lot_id (other warehouse)
--   adjust    qty_g < 0 written off lot_id (shortage, damage)
--             qty_g > 0 found against lot_id, arriving in to_lot_id
CREATE TABLE IF NOT EXISTS stock_moves (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('transfer','adjust')),
  lot_id       INTEGER NOT NULL REFERENCES lots(id),
  to_lot_id    INTEGER REFERENCES lots(id),
  qty_g        INTEGER NOT NULL CHECK (qty_g != 0),
  reason       TEXT,
  move_date    TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('done','cancelled')),
  created_at   TEXT NOT NULL,
  cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_moves_lot ON stock_moves(lot_id, status);
CREATE INDEX IF NOT EXISTS ix_moves_to  ON stock_moves(to_lot_id, status);

-- The last price signal per product, for open P&L.
CREATE TABLE IF NOT EXISTS marks (
  product_id INTEGER PRIMARY KEY REFERENCES products(id),
  rate_paise INTEGER NOT NULL,
  source     TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY,
  ts        TEXT NOT NULL,
  actor     TEXT NOT NULL,
  entity    TEXT NOT NULL,
  entity_id INTEGER,
  action    TEXT NOT NULL,
  summary   TEXT NOT NULL,
  payload   TEXT NOT NULL,
  undone    INTEGER NOT NULL DEFAULT 0,
  undoable  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(id DESC);

-- ================================================================ views

-- A product with its names resolved. Every read that shows a product joins
-- this; nothing stores "PVC S65 · DCW" as text.
DROP VIEW IF EXISTS v_products;
CREATE VIEW v_products AS
  SELECT p.id, p.grade_id, g.material_id, p.manufacturer_id, p.packing, p.created_at,
         m.name AS material, g.name AS grade, k.name AS manufacturer,
         m.name || ' ' || g.name || ' · ' || k.name AS display
  FROM products p
  JOIN grades g        ON g.id = p.grade_id
  JOIN materials m     ON m.id = g.material_id
  JOIN manufacturers k ON k.id = p.manufacturer_id;
