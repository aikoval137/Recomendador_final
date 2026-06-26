import time
import random
import re
import sys
import requests
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_CSV      = "coursera_cursos_v5.csv"
PAGE_SIZE       = 100
LIMITE_CURSOS   = None
SCRAPE_DETALLES = True
LIMITE_DETALLES = None
HEADLESS        = True

IDIOMAS_PERMITIDOS = ["en", "es"]

DEBUG_API_FIELDS = True

MAX_FALLOS_SEGUIDOS   = 5
ESPERA_TRAS_FALLO_S    = 60

URL_CATALOG = "https://api.coursera.org/api/courses.v1"

FIELDS_V1 = (
    "description,instructorIds,partnerIds,workload,"
    "primaryLanguages,courseType,domainTypes,"
    "avgProductRating,numProductRatings,difficultyLevel,"
    "isCourseFree,skills,"
    "rating,numRatings,averageRating,avgRating,"
    "courseLevel,level,"
    "isFree,free,pricing,"
    "primarySkills,relatedSkills,skillTags"
)
INCLUDES_V1 = "instructorIds,partnerIds"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

def limpiar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)
    return " ".join(texto.split())

def limpiar_lista(valores) -> str:
    if not valores:
        return ""
    if isinstance(valores, list):
        return " | ".join(str(v).strip() for v in valores if v)
    return str(valores).strip()

def normalizar_nivel(nivel_raw: str) -> str:
    mapa = {
        "BEGINNER":     "Principiante",
        "INTERMEDIATE": "Intermedio",
        "ADVANCED":     "Avanzado",
        "MIXED":        "Mixto / Todos los niveles",
    }
    return mapa.get(str(nivel_raw).upper(), nivel_raw or "")

def normalizar_precio(is_free) -> str:
    if is_free is True:  return "Gratis"
    if is_free is False: return "De pago (Coursera Plus)"
    return ""

def detectar_tipo_url(url: str) -> str:
    if not url:
        return "curso"
    if "/projects/" in url:
        return "proyecto"
    if "/specializations/" in url:
        return "especializacion"
    return "curso"

def idioma_permitido(idioma_texto: str) -> bool:
    if not idioma_texto:
        return True
    t = idioma_texto.lower()
    return any(re.search(rf"\b{idi}\b", t) or idi in t for idi in IDIOMAS_PERMITIDOS)

def cargar_csv_existente() -> pd.DataFrame:
    path = Path(OUTPUT_CSV)
    if path.exists():
        try:
            df = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
            return df
        except Exception as e:
            print(f" No se pudo leer el CSV existente ({e}), arranco de cero")
    return pd.DataFrame()

def guardar_csv(registros: list[dict]):
    pd.DataFrame(registros).to_csv(OUTPUT_CSV, sep=";", index=False, encoding="utf-8-sig")

def merge_con_existente(nuevos: list[dict], existente: pd.DataFrame) -> list[dict]:
    if existente.empty:
        return nuevos

    existente_por_id = {
        row["id_coursera"]: row.to_dict()
        for _, row in existente.iterrows()
        if row.get("id_coursera")
    }

    fusionados = []
    for curso in nuevos:
        prev = existente_por_id.get(curso.get("id_coursera"))
        if prev and (prev.get("tipo_url") or "").strip():
            campos_detalle = [
                "tipo_url", "headline", "num_inscritos", "horas_semana",
                "duracion_semanas", "duracion_total", "cronograma",
                "tipo_programa", "que_aprenderas", "habilidades",
                "herramientas", "requisitos", "temario", "temario_stats", "url",
                "rating", "num_reseñas", "precio",
            ]
            for c in campos_detalle:
                if c in prev:
                    curso[c] = prev[c]
        fusionados.append(curso)
    return fusionados

def fetch_pagina_catalogo(start: int) -> dict:
    params = {
        "start": start, "limit": PAGE_SIZE,
        "fields": FIELDS_V1, "includes": INCLUDES_V1,
    }
    try:
        r = requests.get(URL_CATALOG, params=params, headers=HEADERS, timeout=20)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        print(f"   ❌ Error de conexión (start={start}): {e}")
        return {}

def primer_valor(item: dict, *claves):
    for clave in claves:
        v = item.get(clave)
        if v is not None and v != "" and v != []:
            return v
    return None

def procesar_elemento(item: dict, instructores: dict, partners: dict) -> dict:
    slug = item.get("slug", "")
    url  = f"https://www.coursera.org/learn/{slug}" if slug else ""

    instructor_nombres = [instructores.get(i, "") for i in (item.get("instructorIds") or [])]
    partner_nombres    = [partners.get(p, "")     for p in (item.get("partnerIds")    or [])]

    rating_raw  = primer_valor(item, "avgProductRating", "rating", "averageRating", "avgRating")
    rating      = f"{rating_raw:.1f}" if isinstance(rating_raw, (int, float)) else (str(rating_raw) if rating_raw else "")

    reseñas_raw = primer_valor(item, "numProductRatings", "numRatings")
    num_reseñas = f"{reseñas_raw:,}" if isinstance(reseñas_raw, int) else (str(reseñas_raw) if reseñas_raw else "")

    nivel_raw = primer_valor(item, "difficultyLevel", "courseLevel", "level")
    is_free   = primer_valor(item, "isCourseFree", "isFree", "free")

    skills_raw = primer_valor(item, "skills", "primarySkills", "relatedSkills", "skillTags")

    domain_types = item.get("domainTypes") or []
    categorias   = limpiar_lista([
        dt.get("domainId", "") + ("/" + dt.get("subdomainId", "") if dt.get("subdomainId") else "")
        for dt in domain_types if isinstance(dt, dict)
    ])

    return {
        "titulo":           item.get("name", ""),
        "tipo_url":         "",
        "headline":         "",
        "instructor":       " | ".join(filter(None, instructor_nombres)),
        "universidad":      " | ".join(filter(None, partner_nombres)),
        "rating":           rating,
        "num_reseñas":      num_reseñas,
        "num_inscritos":    "",
        "horas_semana":     "",
        "duracion_semanas": "",
        "duracion_total":   "",
        "duracion_api":     item.get("workload", ""),
        "cronograma":       "",
        "nivel":            normalizar_nivel(nivel_raw) if nivel_raw is not None else "",
        "precio":           normalizar_precio(is_free) if is_free is not None else "",
        "url":              url,
        "idioma":           limpiar_lista(item.get("primaryLanguages") or []),
        "descripcion":      item.get("description", ""),
        "objetivos_api":    limpiar_lista(skills_raw) if skills_raw else "",
        "que_aprenderas":   "",
        "habilidades":      "",
        "herramientas":     "",
        "requisitos":       "",
        "temario":          "",
        "temario_stats":    "",
        "tipo_curso_api":   item.get("courseType", ""),
        "categorias":       categorias,
        "slug":             slug,
        "id_coursera":      item.get("id", ""),
    }

def etapa1_api() -> list[dict]:
    todos = []
    start = 0
    pagina = 0
    total_api = None

    print("=" * 65)
    print("ETAPA 1 — API pública courses.v1")
    print("=" * 65)

    while True:
        pagina += 1
        data = fetch_pagina_catalogo(start)
        if not data:
            break

        elementos = data.get("elements", [])
        paging    = data.get("paging", {})
        if total_api is None:
            total_api = paging.get("total")

        if not elementos:
            print(f"   🏁 Sin más elementos en página {pagina}.")
            break

        if DEBUG_API_FIELDS and pagina == 1:
            import json as _json
            primero = elementos[0]
            print(f"\n   🔬 DEBUG — claves crudas del primer curso ('{primero.get('name','')[:40]}'):")
            for k in sorted(primero.keys()):
                v = primero[k]
                v_str = str(v)
                if len(v_str) > 80:
                    v_str = v_str[:80] + "..."
                print(f"      {k:22s} = {v_str}")
            try:
                Path("debug_api_raw.json").write_text(
                    _json.dumps(elementos[0], ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"   💾 JSON crudo del primer
