PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------- settings
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ---------------------------------------------------------------- parties
-- One table. A firm can be supplier AND customer AND transporter.
CREATE TABLE IF NOT EXISTS parties (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  slug           TEXT NOT NULL UNIQUE,
  is_supplier    INTEGER NOT NULL DEFAULT 0,
  is_customer    INTEGER NOT NULL DEFAULT 0,
  is_transporter INTEGER NOT NULL DEFAULT 0,
  city           TEXT,
  phone          TEXT,
  address        TEXT,
  gstin          TEXT,          -- unique when present; see db.migrate for the index
  pan            TEXT,          -- derived from GSTIN when there is one
  notes          TEXT,
  created_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------- skus
-- The tradeable atom, and the unit inventory is kept in:
--
--     material  ->  grade  ->  manufacturer
--     PVC           HS1000     Chemplast Sanmar
--
-- All three together are the identity. PVC S65 from Reliance and PVC S65 from
-- DCW are different stock at different prices to different buyers, so they are
-- different rows here and never pool into one position.
CREATE TABLE IF NOT EXISTS skus (
  id           INTEGER PRIMARY KEY,
  slug         TEXT NOT NULL UNIQUE,
  display      TEXT NOT NULL,
  material     TEXT NOT NULL,
  grade        TEXT NOT NULL,
  manufacturer TEXT NOT NULL DEFAULT '',
  packing      TEXT,
  created_at   TEXT NOT NULL,
  UNIQUE (material, grade, manufacturer)
);
CREATE INDEX IF NOT EXISTS ix_skus_tree ON skus(material, grade, manufacturer);

-- ---------------------------------------------------------------- catalogue
-- The master tree, maintained from the Setup screen. A row here can exist with
-- no stock and no deals behind it, which is the point: the trader registers the
-- grades and manufacturers he deals in once, and the ticket then offers them as
-- a closed list instead of a free-text box that fragments the same maker into
-- "Reliance", "reliance " and "RIL".
CREATE TABLE IF NOT EXISTS catalog_materials (
  name       TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_grades (
  material   TEXT NOT NULL,
  grade      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (material, grade)
);
CREATE TABLE IF NOT EXISTS catalog_makers (
  material     TEXT NOT NULL,
  grade        TEXT NOT NULL,
  manufacturer TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (material, grade, manufacturer)
);

-- ---------------------------------------------------------------- deals
-- BUY and SELL live in one table. Terms are record-only (no cost math).
CREATE TABLE IF NOT EXISTS deals (
  id             INTEGER PRIMARY KEY,
  ref            TEXT NOT NULL UNIQUE,
  side           TEXT NOT NULL CHECK (side IN ('buy','sell')),
  status         TEXT NOT NULL CHECK (status IN ('draft','booked','cancelled')),
  party_id       INTEGER NOT NULL REFERENCES parties(id),
  sku_id         INTEGER NOT NULL REFERENCES skus(id),
  qty_g          INTEGER NOT NULL CHECK (qty_g > 0),
  rate_paise     INTEGER NOT NULL CHECK (rate_paise >= 0),
  plus_gst       INTEGER NOT NULL DEFAULT 1,
  deal_date      TEXT NOT NULL,
  -- record-only logistics
  transporter_id INTEGER REFERENCES parties(id),
  freight_by     TEXT,
  delivery_by    TEXT,
  payment_terms  TEXT,
  eway           TEXT,
  remarks        TEXT,
  warehouse      TEXT,          -- buy: where it lands; sell: where it leaves from
  payment_due    TEXT,          -- ISO date the money is due
  ex_place       TEXT,          -- pricing basis, e.g. Ex-Mundra (record only)
  -- sell-side bookkeeping
  alloc_policy   TEXT,
  uncovered_g    INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  booked_at      TEXT,
  cancelled_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_deals_sku    ON deals(sku_id, status);
CREATE INDEX IF NOT EXISTS ix_deals_party  ON deals(party_id, status);
CREATE INDEX IF NOT EXISTS ix_deals_date   ON deals(deal_date DESC, id DESC);

-- ---------------------------------------------------------------- lots
-- Every BUY that is booked creates exactly one lot. Inventory = sum of lots.
CREATE TABLE IF NOT EXISTS lots (
  id              INTEGER PRIMARY KEY,
  label           TEXT NOT NULL,
  deal_id         INTEGER NOT NULL REFERENCES deals(id),
  sku_id          INTEGER NOT NULL REFERENCES skus(id),
  supplier_id     INTEGER NOT NULL REFERENCES parties(id),
  rate_paise      INTEGER NOT NULL,
  qty_g           INTEGER NOT NULL CHECK (qty_g > 0),
  qty_allocated_g INTEGER NOT NULL DEFAULT 0 CHECK (qty_allocated_g >= 0),
  status          TEXT NOT NULL CHECK (status IN ('open','exhausted','cancelled')),
  warehouse       TEXT,         -- where this lot physically sits
  booked_at       TEXT NOT NULL,
  CHECK (qty_allocated_g <= qty_g)
);
CREATE INDEX IF NOT EXISTS ix_lots_sku ON lots(sku_id, status);

-- ---------------------------------------------------------------- warehouses
-- Stock locations, maintained from Setup. A location belongs to a LOT, not to
-- a stock line: the same PVC HS1000 can sit in Mundra and in Aslali at once,
-- and a sale picks warehouses by picking lots.
CREATE TABLE IF NOT EXISTS warehouses (
  name       TEXT PRIMARY KEY,
  location   TEXT,
  created_at TEXT NOT NULL
);

-- ---------------------------------------------------------------- allocations
-- The edge of the lineage graph: lot --qty--> sale deal.
-- cost/rate are SNAPSHOTS so history never changes silently.
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

-- ---------------------------------------------------------------- marks
-- Current market rate per SKU, for unrealised P&L.
CREATE TABLE IF NOT EXISTS marks (
  sku_id     INTEGER PRIMARY KEY REFERENCES skus(id),
  rate_paise INTEGER NOT NULL,
  source     TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- ---------------------------------------------------------------- events
-- Append-only audit trail. Nothing mutates without a row here.
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
