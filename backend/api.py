"""HTTP surface. Thin: parse, call a service, return JSON.

One contract for every list: GET /api/<things>?q=&limit=&offset=&<filters>
answers {items, total, limit, offset, has_more}. One contract for every master
record: POST /api/<things> with an id edits it, without one creates it, and the
saved record comes back so a form can hand it straight to the ticket that
opened it. POST /api/<things>/<id>/remove refuses while anything refers to it.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, security
from .services import (allocation, dashboard, deals, inventory, parties, products, stock,
                       warehouses)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

app = FastAPI(title="Labdhi Trading Desk", version="2.0")

_origins = [o.strip() for o in os.environ.get("LABDHI_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=_origins, allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    if os.environ.get("LABDHI_SEED") == "1" and not db.scalar("SELECT COUNT(*) FROM deals"):
        from .seed import run as seed_run
        seed_run()


app.middleware("http")(security.guard)


@app.get("/api/health")
def health():
    return {"ok": True, "locked": bool(security.token())}


@app.middleware("http")
async def no_stale_assets(request, call_next):
    """Never let a browser hold on to an old build."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.exception_handler(deals.DealError)
def _deal_error(_request, exc: deals.DealError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(ValueError)
def _value_error(_request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


def _found(record, what: str):
    if record is None:
        raise HTTPException(404, "No such %s" % what)
    return record


# ================================================================== summary
@app.get("/api/bootstrap")
def bootstrap() -> Dict[str, Any]:
    """What the first screen needs, in one call."""
    return {"settings": db.settings(), "summary": dashboard.summary(), "undo": deals.last_undoable()}


@app.get("/api/summary")
def summary() -> Dict[str, Any]:
    return dashboard.summary()


# ================================================================== parties
class PartyIn(BaseModel):
    id: Optional[int] = None
    name: str
    phone: Optional[str] = ""
    address: Optional[str] = ""
    gstin: Optional[str] = ""
    pan: Optional[str] = ""                # ignored when a GSTIN is given
    state_code: Optional[str] = ""         # ignored when a GSTIN is given


@app.get("/api/parties")
def list_parties(q: str = "", state_code: Optional[str] = None, holding: bool = False,
                 limit: Optional[int] = None, offset: int = 0):
    return parties.list_parties(q, state_code, holding, limit, offset)


@app.get("/api/parties/{party_id}")
def get_party(party_id: int):
    return _found(parties.get_party(party_id), "party")


@app.post("/api/parties")
def save_party(body: PartyIn):
    with db.tx() as conn:
        pid = parties.save_party(conn, name=body.name, phone=body.phone or "",
                                 address=body.address or "", gstin=body.gstin or "",
                                 pan=body.pan or "", state_code=body.state_code or "",
                                 party_id=body.id)
    return parties.get_party(pid)


@app.post("/api/parties/{party_id}/remove")
def remove_party(party_id: int):
    with db.tx() as conn:
        parties.remove_party(conn, party_id)
    return {"removed": party_id}


@app.get("/api/gstin/{gstin}")
def check_gstin(gstin: str):
    return parties.check_gstin(gstin)


@app.get("/api/states")
def list_states(q: str = "", limit: Optional[int] = None, offset: int = 0):
    return parties.list_states(q, limit, offset)


# ================================================================== warehouses
class WarehouseIn(BaseModel):
    id: Optional[int] = None
    name: str
    address: Optional[str] = None


@app.get("/api/warehouses")
def list_warehouses(q: str = "", product_id: Optional[int] = None, in_stock: bool = False,
                    limit: Optional[int] = None, offset: int = 0):
    return warehouses.list_warehouses(q, product_id, in_stock, limit, offset)


@app.get("/api/warehouses/{warehouse_id}")
def get_warehouse(warehouse_id: int):
    return _found(warehouses.get_warehouse(warehouse_id), "warehouse")


@app.post("/api/warehouses")
def save_warehouse(body: WarehouseIn):
    with db.tx() as conn:
        wid = warehouses.save_warehouse(conn, body.name, body.address, body.id)
    return warehouses.get_warehouse(wid)


@app.post("/api/warehouses/{warehouse_id}/remove")
def remove_warehouse(warehouse_id: int):
    with db.tx() as conn:
        warehouses.remove_warehouse(conn, warehouse_id)
    return {"removed": warehouse_id}


# ================================================================== product tree
class NameIn(BaseModel):
    id: Optional[int] = None
    name: str
    material_id: Optional[int] = None      # grades only


class ProductIn(BaseModel):
    id: Optional[int] = None
    material: str
    grade: str
    manufacturer: str
    packing: Optional[str] = None


@app.get("/api/materials")
def list_materials(q: str = "", has_products: bool = False, in_stock: bool = False,
                   limit: Optional[int] = None, offset: int = 0):
    return products.list_materials(q, has_products, in_stock, limit, offset)


@app.post("/api/materials")
def save_material(body: NameIn):
    with db.tx() as conn:
        mid = products.save_material(conn, body.name, body.id)
    return {"id": mid}


@app.post("/api/materials/{material_id}/remove")
def remove_material(material_id: int):
    with db.tx() as conn:
        products.remove_material(conn, material_id)
    return {"removed": material_id}


@app.get("/api/grades")
def list_grades(material_id: Optional[int] = None, q: str = "", has_products: bool = False,
                in_stock: bool = False, limit: Optional[int] = None, offset: int = 0):
    return products.list_grades(material_id, q, has_products, in_stock, limit, offset)


@app.post("/api/grades")
def save_grade(body: NameIn):
    with db.tx() as conn:
        gid = products.save_grade(conn, body.material_id, body.name, body.id)
    return {"id": gid}


@app.post("/api/grades/{grade_id}/remove")
def remove_grade(grade_id: int):
    with db.tx() as conn:
        products.remove_grade(conn, grade_id)
    return {"removed": grade_id}


@app.get("/api/manufacturers")
def list_manufacturers(q: str = "", limit: Optional[int] = None, offset: int = 0):
    return products.list_manufacturers(q, limit, offset)


@app.post("/api/manufacturers")
def save_manufacturer(body: NameIn):
    with db.tx() as conn:
        kid = products.save_manufacturer(conn, body.name, body.id)
    return {"id": kid}


@app.post("/api/manufacturers/{manufacturer_id}/remove")
def remove_manufacturer(manufacturer_id: int):
    with db.tx() as conn:
        products.remove_manufacturer(conn, manufacturer_id)
    return {"removed": manufacturer_id}


@app.get("/api/products")
def list_products(q: str = "", material_id: Optional[int] = None, grade_id: Optional[int] = None,
                  manufacturer_id: Optional[int] = None, warehouse_id: Optional[int] = None,
                  in_stock: bool = False, limit: Optional[int] = None, offset: int = 0):
    return products.list_products(q, material_id, grade_id, manufacturer_id, warehouse_id,
                                  in_stock, limit, offset)


@app.get("/api/products/{product_id}")
def get_product(product_id: int):
    return _found(products.get_product(product_id), "product")


@app.post("/api/products")
def save_product(body: ProductIn):
    with db.tx() as conn:
        res = products.save_product(conn, body.material, body.grade, body.manufacturer,
                                    body.packing, body.id)
    out = products.get_product(res["id"])
    out["existed"] = res["existed"]
    return out


@app.post("/api/products/{product_id}/remove")
def remove_product(product_id: int):
    with db.tx() as conn:
        products.remove_product(conn, product_id)
    return {"removed": product_id}


# ================================================================== stock
class TransferIn(BaseModel):
    lot_id: int
    to_warehouse_id: int
    qty_g: int
    move_date: Optional[str] = None
    reason: Optional[str] = None


class AdjustIn(BaseModel):
    lot_id: int
    qty_g: int                             # negative writes off, positive finds
    move_date: Optional[str] = None
    reason: Optional[str] = None


@app.get("/api/positions")
def list_positions(q: str = "", include_flat: bool = False, warehouse_id: Optional[int] = None,
                   material_id: Optional[int] = None, grade_id: Optional[int] = None,
                   manufacturer_id: Optional[int] = None, supplier_id: Optional[int] = None,
                   limit: Optional[int] = None, offset: int = 0):
    return inventory.positions(include_flat=include_flat, q=q, limit=limit, offset=offset,
                               warehouse_id=warehouse_id, material_id=material_id,
                               grade_id=grade_id, manufacturer_id=manufacturer_id,
                               supplier_id=supplier_id)


@app.get("/api/positions/{product_id}")
def get_position(product_id: int):
    return _found(inventory.position_detail(product_id), "product")


@app.get("/api/stock")
def list_stock(q: str = "", product_id: Optional[int] = None, warehouse_id: Optional[int] = None,
               material_id: Optional[int] = None, grade_id: Optional[int] = None,
               manufacturer_id: Optional[int] = None, limit: Optional[int] = None, offset: int = 0):
    return stock.stock_rows(q, product_id, warehouse_id, material_id, grade_id, manufacturer_id,
                            limit, offset)


@app.get("/api/stock/lots")
def stock_lots(product_id: int, warehouse_id: Optional[int] = None):
    """Every open lot of a product (in one warehouse) - the set a sale is split across."""
    return {"items": stock.lots_for(product_id, warehouse_id)}


@app.get("/api/stock/moves")
def stock_moves(product_id: Optional[int] = None, warehouse_id: Optional[int] = None,
                lot_id: Optional[int] = None, limit: Optional[int] = None, offset: int = 0):
    return stock.movements(product_id, warehouse_id, lot_id, limit, offset)


@app.post("/api/stock/transfer")
def transfer(body: TransferIn):
    return stock.transfer(body.lot_id, body.to_warehouse_id, body.qty_g, body.move_date, body.reason)


@app.post("/api/stock/adjust")
def adjust(body: AdjustIn):
    return stock.adjust(body.lot_id, body.qty_g, body.move_date, body.reason)


@app.post("/api/stock/moves/{move_id}/cancel")
def cancel_move(move_id: int):
    return stock.cancel_move(move_id)


@app.get("/api/graph")
def graph(product_id: Optional[int] = None, warehouse_id: Optional[int] = None,
          date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 60):
    return inventory.graph(product_id, date_from, date_to, limit, warehouse_id)


@app.get("/api/trace/{kind}/{entity_id}")
def trace(kind: str, entity_id: int):
    if kind not in ("lot", "sale"):
        raise HTTPException(400, "kind must be lot or sale")
    return inventory.trace(kind, entity_id)


# ================================================================== deals
class DealIn(BaseModel):
    side: str
    party_id: Optional[int] = None
    product_id: Optional[int] = None
    warehouse_id: Optional[int] = None     # buy: receiving; sell: dispatching
    qty_g: int
    rate_paise: int
    plus_gst: bool = True
    deal_date: Optional[str] = None
    sauda_no: Optional[str] = None         # blank means "issue the next one"
    payment_due: Optional[str] = None
    ex_place: Optional[str] = None
    transporter_id: Optional[int] = None
    freight_by: Optional[str] = None
    delivery_by: Optional[str] = None
    payment_terms: Optional[str] = None
    eway: Optional[str] = None
    remarks: Optional[str] = None
    policy: Optional[str] = None
    pins: Optional[List[Dict[str, int]]] = None
    allow_short: bool = False
    confirm: bool = True


class PreviewIn(BaseModel):
    product_id: int
    warehouse_id: Optional[int] = None
    qty_g: int
    rate_paise: int = 0
    policy: str = allocation.DEFAULT_POLICY
    pins: Optional[List[Dict[str, int]]] = None


class ReallocIn(BaseModel):
    pins: Optional[List[Dict[str, int]]] = None
    policy: Optional[str] = None


@app.get("/api/deals")
def list_deals(q: str = "", side: Optional[str] = None, status: Optional[str] = None,
               party_id: Optional[int] = None, product_id: Optional[int] = None,
               warehouse_id: Optional[int] = None, material_id: Optional[int] = None,
               grade_id: Optional[int] = None, manufacturer_id: Optional[int] = None,
               date_from: Optional[str] = None, date_to: Optional[str] = None,
               limit: Optional[int] = None, offset: int = 0):
    return deals.list_deals(limit=limit, offset=offset, q=q, side=side, status=status,
                            party_id=party_id, product_id=product_id, warehouse_id=warehouse_id,
                            material_id=material_id, grade_id=grade_id,
                            manufacturer_id=manufacturer_id, date_from=date_from, date_to=date_to)


@app.get("/api/deals/{deal_id}")
def get_deal(deal_id: int):
    return _found(deals.get_deal(deal_id), "deal")


@app.post("/api/deals")
def create_deal(body: DealIn) -> Dict[str, Any]:
    return deals.create_deal(body.dict())


@app.post("/api/deals/{deal_id}/book")
def book(deal_id: int, body: ReallocIn) -> Dict[str, Any]:
    return deals.book_deal(deal_id, pins=body.pins, policy=body.policy)


@app.post("/api/deals/{deal_id}/reallocate")
def reallocate(deal_id: int, body: ReallocIn) -> Dict[str, Any]:
    return deals.reallocate(deal_id, pins=body.pins, policy=body.policy)


@app.post("/api/deals/{deal_id}/cancel")
def cancel(deal_id: int, reason: str = "") -> Dict[str, Any]:
    return deals.cancel_deal(deal_id, reason)


@app.post("/api/preview/sell")
def preview_sell(body: PreviewIn) -> Dict[str, Any]:
    """Live margin as the trader moves the quantity and rate. No writes."""
    plan = allocation.preview(body.product_id, body.qty_g, body.rate_paise, body.policy,
                              pins=body.pins, warehouse_id=body.warehouse_id)
    plan["lots"] = stock.lots_for(body.product_id, body.warehouse_id)
    return plan


@app.get("/api/sauda/next")
def sauda_next(date: Optional[str] = None):
    return {"sauda_no": deals.next_sauda_no(date)}


@app.get("/api/counterparties")
def counterparties(limit: Optional[int] = None, offset: int = 0):
    return dashboard.counterparties(limit, offset)


@app.post("/api/undo")
def undo(event_id: Optional[int] = None) -> Dict[str, Any]:
    if event_id is None:
        ev = deals.last_undoable()
        if not ev:
            raise HTTPException(400, "Nothing to undo")
        event_id = int(ev["id"])
    return dict(undone=event_id, **deals.undo_event(event_id))


@app.get("/api/events")
def events(limit: Optional[int] = None, offset: int = 0):
    limit, offset = db.page_args(limit, offset, default=60)
    total = db.scalar("SELECT COUNT(*) FROM events")
    rows = db.q("SELECT * FROM events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    return db.page(db.dicts(rows), total, limit, offset)


# ================================================================== settings
class MarkIn(BaseModel):
    rate_paise: int


class SettingsIn(BaseModel):
    company_name: Optional[str] = None
    sauda_prefix: Optional[str] = None
    alloc_policy: Optional[str] = None
    allow_short_sales: Optional[bool] = None


@app.post("/api/marks/{product_id}")
def set_mark(product_id: int, body: MarkIn) -> Dict[str, Any]:
    products.require(product_id)
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO marks(product_id,rate_paise,source,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(product_id) DO UPDATE SET rate_paise=excluded.rate_paise, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (product_id, body.rate_paise, "manual", db.now()))
        db.log(conn, "product", product_id, "mark", "Marked at %d" % body.rate_paise,
               {"rate_paise": body.rate_paise})
    return {"product_id": product_id, "rate_paise": body.rate_paise}


@app.post("/api/settings")
def save_settings(body: SettingsIn) -> Dict[str, str]:
    with db.tx() as conn:
        for key, value in body.dict(exclude_none=True).items():
            db.set_setting(conn, key, "1" if value is True else ("0" if value is False else value))
    return db.settings()


# ================================================================== static
def build_id() -> str:
    """A token that changes whenever any front-end file changes."""
    latest = 0.0
    for root, _dirs, files in os.walk(WEB):
        for name in files:
            try:
                latest = max(latest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                pass
    return format(int(latest * 1000), "x")


@app.get("/")
def index():
    """Serve the shell with every asset URL under a build-stamped prefix.

    A module at /b/<build>/js/app.js resolves its own imports under the same
    prefix, so the whole module graph is versioned by one substitution and a
    stale JS file becomes structurally impossible.
    """
    with open(os.path.join(WEB, "index.html")) as fh:
        html = fh.read()
    prefix = "/b/%s" % build_id()
    html = html.replace('href="/css/', 'href="%s/css/' % prefix)
    html = html.replace('src="/js/', 'src="%s/js/' % prefix)
    return HTMLResponse(html)


@app.get("/b/{build}/{asset_path:path}")
def versioned_asset(build: str, asset_path: str):
    full = os.path.normpath(os.path.join(WEB, asset_path))
    if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
        raise HTTPException(404, "No such asset")
    return FileResponse(full)


app.mount("/", StaticFiles(directory=WEB), name="web")
