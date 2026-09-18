"""FastAPI 入口：浏览器表单页面 + JSON API + 报告导出。"""
from __future__ import annotations

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, report as report_mod
from .review import compare, review

app = FastAPI(title="食品过敏原标签审查台")

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db(seed=True)


def _ctx(request: Request, **kw) -> dict:
    ctx = {"request": request}
    ctx.update(kw)
    return ctx


def _download_response(content: str, filename: str, fmt: str) -> Response:
    media = "text/markdown; charset=utf-8" if fmt == "md" else "text/plain; charset=utf-8"
    data = content.encode("utf-8")
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------- 首页 / 单次审查

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", _ctx(
        request, result=None, name="", version="", ingredients="", declared=""))


@app.post("/review")
def do_review(
    request: Request,
    name: str = Form(""),
    version: str = Form(""),
    ingredients: str = Form(...),
    declared: str = Form(""),
    save: str = Form(""),
):
    knowledge = db.load_knowledge()
    result = review(ingredients, declared, knowledge)
    recipe_id = None
    if save == "1":
        recipe_id = db.create_recipe(name, version, ingredients, declared)
    return templates.TemplateResponse("index.html", _ctx(
        request, result=result, name=name, version=version,
        ingredients=ingredients, declared=declared,
        recipe_id=recipe_id, just_saved=bool(recipe_id)))


# ---------------------------------------------------------------- 已存配方

@app.get("/recipes", response_class=HTMLResponse)
def recipes_page(request: Request):
    return templates.TemplateResponse("recipes.html", _ctx(request, recipes=db.list_recipes()))


@app.get("/recipes/{recipe_id}", response_class=HTMLResponse)
def recipe_detail(request: Request, recipe_id: int):
    row = db.get_recipe(recipe_id)
    if row is None:
        return RedirectResponse("/recipes", status_code=302)
    result = review(row["ingredients_text"], row["declared_text"], db.load_knowledge())
    return templates.TemplateResponse("index.html", _ctx(
        request, result=result, name=row["name"], version=row["version"],
        ingredients=row["ingredients_text"], declared=row["declared_text"],
        recipe_id=recipe_id, created_at=row["created_at"]))


@app.post("/recipes/{recipe_id}/delete")
def recipe_delete(recipe_id: int):
    db.delete_recipe(recipe_id)
    return RedirectResponse("/recipes", status_code=303)


# ---------------------------------------------------------------- 两版对比

@app.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    return templates.TemplateResponse("compare.html", _ctx(
        request, cmp=None, recipes=db.list_recipes(),
        old_ingredients="", old_declared="", old_version="",
        new_ingredients="", new_declared="", new_version="", name=""))


@app.post("/compare")
def do_compare(
    request: Request,
    name: str = Form(""),
    old_version: str = Form(""),
    old_ingredients: str = Form(""),
    old_declared: str = Form(""),
    new_version: str = Form(""),
    new_ingredients: str = Form(""),
    new_declared: str = Form(""),
    old_recipe_id: str = Form(""),
    new_recipe_id: str = Form(""),
):
    # 选择了已存配方则用库里的内容覆盖
    if old_recipe_id:
        r = db.get_recipe(int(old_recipe_id))
        if r:
            old_ingredients, old_declared, old_version = r["ingredients_text"], r["declared_text"], r["version"]
    if new_recipe_id:
        r = db.get_recipe(int(new_recipe_id))
        if r:
            new_ingredients, new_declared, new_version = r["ingredients_text"], r["declared_text"], r["version"]

    knowledge = db.load_knowledge()
    old_r = review(old_ingredients, old_declared, knowledge)
    new_r = review(new_ingredients, new_declared, knowledge)
    cmp_result = compare(old_r, new_r)
    return templates.TemplateResponse("compare.html", _ctx(
        request, cmp=cmp_result, recipes=db.list_recipes(), name=name,
        old_version=old_version, old_ingredients=old_ingredients, old_declared=old_declared,
        new_version=new_version, new_ingredients=new_ingredients, new_declared=new_declared))


# ---------------------------------------------------------------- 报告导出

@app.get("/report")
def report_single(
    recipe_id: int = Query(...),
    fmt: str = Query("md"),
):
    row = db.get_recipe(recipe_id)
    if row is None:
        return RedirectResponse("/recipes", status_code=302)
    result = review(row["ingredients_text"], row["declared_text"], db.load_knowledge())
    content = report_mod.build_single_report(
        result, row["name"], row["version"],
        row["ingredients_text"], row["declared_text"], fmt=fmt)
    return _download_response(content, f"allergen_report_{recipe_id}.{fmt}", fmt)


@app.get("/report/compare")
def report_compare_saved(
    old_id: int = Query(...),
    new_id: int = Query(...),
    fmt: str = Query("md"),
):
    old_row, new_row = db.get_recipe(old_id), db.get_recipe(new_id)
    if old_row is None or new_row is None:
        return RedirectResponse("/recipes", status_code=302)
    knowledge = db.load_knowledge()
    cmp_result = compare(
        review(old_row["ingredients_text"], old_row["declared_text"], knowledge),
        review(new_row["ingredients_text"], new_row["declared_text"], knowledge))
    content = report_mod.build_compare_report(
        cmp_result, new_row["name"] or old_row["name"],
        old_row["ingredients_text"], old_row["declared_text"],
        new_row["ingredients_text"], new_row["declared_text"],
        old_row["version"], new_row["version"], fmt=fmt)
    return _download_response(content, f"allergen_compare_{old_id}_vs_{new_id}.{fmt}", fmt)


@app.post("/report/adhoc")
def report_adhoc(
    kind: str = Form("single"),
    fmt: str = Form("md"),
    name: str = Form(""),
    version: str = Form(""),
    ingredients: str = Form(""),
    declared: str = Form(""),
    old_version: str = Form(""),
    old_ingredients: str = Form(""),
    old_declared: str = Form(""),
    new_version: str = Form(""),
    new_ingredients: str = Form(""),
    new_declared: str = Form(""),
):
    """导出当前（未保存）表单内容的报告。"""
    knowledge = db.load_knowledge()
    if kind == "compare":
        cmp_result = compare(
            review(old_ingredients, old_declared, knowledge),
            review(new_ingredients, new_declared, knowledge))
        content = report_mod.build_compare_report(
            cmp_result, name, old_ingredients, old_declared,
            new_ingredients, new_declared, old_version, new_version, fmt=fmt)
        return _download_response(content, f"allergen_compare.{fmt}", fmt)
    result = review(ingredients, declared, knowledge)
    content = report_mod.build_single_report(
        result, name, version, ingredients, declared, fmt=fmt)
    return _download_response(content, f"allergen_report.{fmt}", fmt)


# ---------------------------------------------------------------- 字典管理

@app.get("/dict", response_class=HTMLResponse)
def dict_page(request: Request):
    return templates.TemplateResponse("dict.html", _ctx(
        request, mappings=db.list_mappings(), allergens=db.list_allergens(),
        error=None))


@app.post("/dict/mapping")
def dict_add_mapping(
    ingredient: str = Form(...),
    allergen: str = Form(""),
    note: str = Form(""),
):
    try:
        db.add_mapping(ingredient, allergen, note)
    except Exception:
        pass  # 重复原料名，忽略
    return RedirectResponse("/dict", status_code=303)


@app.post("/dict/mapping/{mapping_id}/update")
def dict_update_mapping(
    mapping_id: int,
    ingredient: str = Form(...),
    allergen: str = Form(""),
    note: str = Form(""),
):
    db.update_mapping(mapping_id, ingredient, allergen, note)
    return RedirectResponse("/dict", status_code=303)


@app.post("/dict/mapping/{mapping_id}/delete")
def dict_delete_mapping(mapping_id: int):
    db.delete_mapping(mapping_id)
    return RedirectResponse("/dict", status_code=303)


@app.post("/dict/allergen")
def dict_add_allergen(
    name: str = Form(...),
    aliases: str = Form(""),
    note: str = Form(""),
):
    try:
        db.add_allergen(name, aliases, note)
    except Exception:
        pass
    return RedirectResponse("/dict", status_code=303)


# ---------------------------------------------------------------- JSON API（便于集成）

@app.get("/api/review")
def api_review(ingredients: str, declared: str = ""):
    result = review(ingredients, declared, db.load_knowledge())
    return {
        "detected_allergens": result.all_detected,
        "missing": result.missing,
        "extra": result.extra,
        "unrecognized_declared": result.unrecognized,
        "label_ok": result.label_ok,
        "balance_error": result.balance_error,
        "leaves": result.leaves,
        "tree": [
            {"depth": e.depth, "name": e.name, "direct": e.direct,
             "propagated": e.propagated, "composite": e.is_composite}
            for e in result.flat
        ],
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
