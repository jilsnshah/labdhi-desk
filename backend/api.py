"""HTTP surface. Thin: parse, call a service, return JSON."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, security
from .money import parse_qty, parse_rate
from .services import allocation, catalog, dashboard, deals, inventory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

app = FastAPI(title="Labdhi Trading Desk", version="1.0")

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
    """Never let a browser hold on to an old build.

    Without an explicit Cache-Control, browsers fall back to heuristic caching
    and will happily keep serving yesterday's JS module without revalidating.
    On a single-user desk app there is nothing to gain from caching and a whole
    class of "I still see the old screen" confusion to lose.
    """
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


# ------------------------------------------------------------------ models
class DealIn(BaseModel):
    side: str
    party_id: Optional[int] = None
    party_name: Optional[str] = None
    sku_id: Optional[int] = None
    material: Optional[str] = None        # PVC
    grade: Optional[str] = None           # HS1000
    manufacturer: Optional[str] = None    # Chemplast Sanmar  (never the supplier)
    packing: Optional[str] = None
    qty: Optional[str] = None
    qty_g: Optional[int] = None
    rate: Optional[str] = None
    rate_paise: Optional[int] = None
    plus_gst: bool = True
    deal_date: Optional[str] = None
    transporter: Optional[str] = None
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
    sku_id: Optional[int] = None
    material: Optional[str] = None
    grade: Optional[str] = None
    manufacturer: Optional[str] = None        # PVC
    grade: Optional[str] = None           # HS1000
    manufacturer: Optional[str] = None    # Chemplast Sanmar  (never the supplier)
    packing: Optional[str] = None
    qty: Optional[str] = None
    qty_g: Optional[int] = None
    rate: Optional[str] = None
    rate_paise: Optional[int] = None
    policy: str = allocation.DEFAULT_POLICY
    pins: Optional[List[Dict[str, int]]] = None
    prefer_supplier: Optional[int] = None
    ignore_sale_id: Optional[int] = None


class ReallocIn(BaseModel):
    pins: Optional[List[Dict[str, int]]] = None
    policy: Optional[str] = None


class MarkIn(BaseModel):
    rate: Optional[str] = None
    rate_paise: Optional[int] = None


class SettingsIn(BaseModel):
    company_name: Optional[str] = None
    alloc_policy: Optional[str] = None
    allow_short_sales: Optional[bool] = None


def _qty_of(m) -> int:
    if m.qty_g:
        return int(m.qty_g)
    if m.qty:
        return parse_qty(m.qty)
    raise ValueError("Quantity is missing")


def _rate_of(m, required: bool = True):
    if m.rate_paise:
        return int(m.rate_paise), bool(getattr(m, "plus_gst", True))
    if m.rate:
        return parse_rate(m.rate)
    if required:
        raise ValueError("Rate is missing")
    return 0, True


def _resolve_sku(m) -> int:
    if m.sku_id:
        return int(m.sku_id)
    if m.material and m.grade:
        with db.tx() as conn:
            return catalog.upsert_sku(conn, material=m.material, grade=m.grade,
                                      manufacturer=m.manufacturer or "")
    raise ValueError("Material, grade and manufacturer are needed")


# ------------------------------------------------------------------ reads
@app.get("/api/bootstrap")
def bootstrap() -> Dict[str, Any]:
    """One call the app opens with - everything the first screen needs."""
    return {
        "settings": db.settings(),
        "desk": dashboard.summary(),
        "tape": dashboard.tape(25)["deals"],
        "suppliers": catalog.search_parties(role="supplier", limit=10),
        "customers": catalog.search_parties(role="customer", limit=10),
        "materials": catalog.search_skus(limit=12),
        "undo": deals.last_undoable(),
    }


@app.get("/api/desk")
def desk(q: str = "", limit: int = dashboard.POSITION_PAGE, offset: int = 0,
         material: Optional[str] = None, grade: Optional[str] = None,
         manufacturer: Optional[str] = None, supplier_id: Optional[int] = None) -> Dict[str, Any]:
    return dashboard.summary(q=q, limit=limit, offset=offset, material=material,
                             grade=grade, manufacturer=manufacturer, supplier_id=supplier_id)


@app.get("/api/tape")
def tape(limit: int = 30, offset: int = 0, q: str = "",
         side: Optional[str] = None, status: Optional[str] = None,
         date_from: Optional[str] = None, date_to: Optional[str] = None,
         material: Optional[str] = None, grade: Optional[str] = None,
         manufacturer: Optional[str] = None) -> Dict[str, Any]:
    page = dashboard.tape(limit, offset, q, side=side, status=status,
                          date_from=date_from, date_to=date_to, material=material,
                          grade=grade, manufacturer=manufacturer)
    if offset == 0:
        page["counterparties"] = dashboard.counterparties()
    return page


@app.get("/api/positions")
def positions(include_flat: bool = False, q: str = "",
              limit: Optional[int] = None, offset: int = 0,
              material: Optional[str] = None, grade: Optional[str] = None,
              manufacturer: Optional[str] = None, supplier_id: Optional[int] = None) -> Dict[str, Any]:
    f = dict(material=material, grade=grade, manufacturer=manufacturer, supplier_id=supplier_id)
    return {
        "positions": inventory.positions(include_flat=include_flat, q=q, limit=limit,
                                         offset=offset, **f),
        "matched": inventory.count_positions(q=q, include_flat=include_flat, **f),
    }


@app.get("/api/positions/{sku_id}")
def position(sku_id: int) -> Dict[str, Any]:
    detail = inventory.position_detail(sku_id)
    if detail is None:
        raise HTTPException(404, "No such material")
    return detail


@app.get("/api/graph")
def graph(sku_id: Optional[int] = None, date_from: Optional[str] = None,
          date_to: Optional[str] = None, limit: int = 60) -> Dict[str, Any]:
    return inventory.graph(sku_id, date_from, date_to, limit)


@app.get("/api/trace/{kind}/{entity_id}")
def trace(kind: str, entity_id: int) -> Dict[str, Any]:
    if kind not in ("lot", "sale"):
        raise HTTPException(400, "kind must be lot or sale")
    return inventory.trace(kind, entity_id)


@app.get("/api/search/parties")
def search_parties(q: str = "", role: Optional[str] = None, limit: int = 8):
    return {"results": catalog.search_parties(q, role, limit)}


@app.get("/api/search/materials")
def search_materials(q: str = "", limit: int = 8, in_stock: bool = False):
    return {"results": catalog.search_skus(q, limit, in_stock_only=in_stock)}


@app.get("/api/catalog/options")
def catalog_options(level: str, material: Optional[str] = None,
                    grade: Optional[str] = None, in_stock: bool = False):
    """One rung of material -> grade -> manufacturer, narrowed by the rungs above."""
    known = {"material": catalog.MATERIALS, "grade": [], "manufacturer": catalog.MAKERS}
    return {
        "options": catalog.options(level, material, grade, in_stock),
        "suggestions": known.get(level, []),
    }


@app.get("/api/catalog/tree")
def catalog_tree():
    """The whole master tree, for the Setup screen."""
    return {"tree": catalog.tree(), "makers": catalog.MAKERS, "materials": catalog.MATERIALS}


class CatalogIn(BaseModel):
    material: str
    grade: Optional[str] = None
    manufacturer: Optional[str] = None


@app.post("/api/catalog/entry")
def catalog_add(body: CatalogIn):
    with db.tx() as conn:
        if body.manufacturer:
            if not body.grade:
                raise ValueError("A manufacturer needs a grade")
            catalog.add_maker(conn, body.material, body.grade, body.manufacturer)
        elif body.grade:
            catalog.add_grade(conn, body.material, body.grade)
        else:
            catalog.add_material(conn, body.material)
    return {"tree": catalog.tree()}


@app.post("/api/catalog/remove")
def catalog_remove(body: CatalogIn):
    with db.tx() as conn:
        catalog.remove(conn, body.material, body.grade, body.manufacturer)
    return {"tree": catalog.tree()}


@app.get("/api/catalog/resolve")
def catalog_resolve(material: str, grade: str, manufacturer: str = ""):
    sku = catalog.resolve(material, grade, manufacturer)
    if sku is None:
        return {"sku": None}
    sku["stock_g"] = db.scalar(
        "SELECT COALESCE(SUM(qty_g - qty_allocated_g),0) FROM lots "
        "WHERE sku_id=? AND status='open'", (sku["id"],))
    return {"sku": sku}


@app.get("/api/lots/{sku_id}")
def lots(sku_id: int):
    return {"lots": allocation.available_lots(sku_id)}


@app.get("/api/deals")
def list_deals(side: Optional[str] = None, status: Optional[str] = None,
               sku_id: Optional[int] = None, party_id: Optional[int] = None,
               limit: int = 60, offset: int = 0, q: str = ""):
    return {
        "deals": deals.list_deals(side, status, sku_id, party_id, limit, offset, q),
        "matched": deals.count_deals(side, status, sku_id, party_id, q),
    }


@app.get("/api/deals/{deal_id}")
def get_deal(deal_id: int):
    d = deals.get_deal(deal_id)
    if d is None:
        raise HTTPException(404, "No such deal")
    return d


@app.get("/api/events")
def events(limit: int = 60):
    rows = db.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    return {"events": [dict(r) for r in rows]}


# ------------------------------------------------------------------ writes
@app.post("/api/preview/sell")
def preview_sell(body: PreviewIn) -> Dict[str, Any]:
    """Live margin as the trader moves the quantity and rate. No writes."""
    sku_id = _resolve_sku(body)
    qty_g = _qty_of(body)
    rate_paise, _ = _rate_of(body, required=False)
    plan = allocation.preview(sku_id, qty_g, rate_paise, body.policy,
                              pins=body.pins, prefer_supplier=body.prefer_supplier,
                              ignore_sale_id=body.ignore_sale_id)
    plan["lots"] = allocation.available_lots(sku_id)
    plan["sku"] = catalog.get_sku(sku_id)
    return plan


@app.post("/api/deals")
def create_deal(body: DealIn) -> Dict[str, Any]:
    payload = body.dict()
    payload["qty_g"] = _qty_of(body)
    rate_paise, plus = _rate_of(body)
    payload["rate_paise"] = rate_paise
    if body.rate:
        payload["plus_gst"] = plus
    return deals.create_deal(payload)


@app.post("/api/deals/{deal_id}/book")
def book(deal_id: int, body: ReallocIn) -> Dict[str, Any]:
    return deals.book_deal(deal_id, pins=body.pins, policy=body.policy)


@app.post("/api/deals/{deal_id}/reallocate")
def reallocate(deal_id: int, body: ReallocIn) -> Dict[str, Any]:
    return deals.reallocate(deal_id, pins=body.pins, policy=body.policy)


@app.post("/api/deals/{deal_id}/cancel")
def cancel(deal_id: int, reason: str = "") -> Dict[str, Any]:
    return deals.cancel_deal(deal_id, reason)


@app.post("/api/undo")
def undo(event_id: Optional[int] = None) -> Dict[str, Any]:
    if event_id is None:
        ev = deals.last_undoable()
        if not ev:
            raise HTTPException(400, "Nothing to undo")
        event_id = int(ev["id"])
    return {"undone": event_id, "deal": deals.undo_event(event_id)}


@app.post("/api/marks/{sku_id}")
def set_mark(sku_id: int, body: MarkIn) -> Dict[str, Any]:
    rate = body.rate_paise if body.rate_paise else parse_rate(body.rate)[0]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO marks(sku_id,rate_paise,source,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(sku_id) DO UPDATE SET rate_paise=excluded.rate_paise, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (sku_id, rate, "manual", db.now()))
        db.log(conn, "sku", sku_id, "mark", "Marked at %d" % rate, {"rate_paise": rate})
    return {"sku_id": sku_id, "rate_paise": rate}


@app.post("/api/settings")
def save_settings(body: SettingsIn) -> Dict[str, str]:
    with db.tx() as conn:
        for key, value in body.dict(exclude_none=True).items():
            db.set_setting(conn, key, "1" if value is True else ("0" if value is False else value))
    return db.settings()


# ------------------------------------------------------------------ static
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

    The prefix goes in the PATH, not a query string, which matters: a module
    at /b/<build>/js/app.js resolves its own `import './ui.js'` to
    /b/<build>/js/ui.js, so the whole module graph is versioned by one
    substitution. Edit any file and the browser is asking for URLs it has
    never seen - stale JS becomes structurally impossible rather than a
    matter of remembering to hard-reload.
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
