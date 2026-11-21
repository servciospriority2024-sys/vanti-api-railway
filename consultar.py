#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
consultar.py - Motor CLI de consulta de facturas Vanti
Soporta detección de facturas pagadas (Saldo 0), redirección a PSE y filtro por empresa
"""
import sys
import os
import re
import time
import json
import argparse
from playwright.sync_api import sync_playwright

for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

SITE = "https://pagosenlinea.grupovanti.com/"

EMPRESAS = [
    ("79", "Vanti S.A. E.S.P."),
    ("80", "Gas Natural Cundiboyacense"),
    ("81", "Gas Natural del Cesar (Nacer)"),
    ("82", "Gas Natural del Oriente"),
    ("84", "Vanti Soluciones")
]

PROXY_CONFIG = {
    "server": "http://proxy.smartproxy.net:3120",
    "username": "smart-zxr9160zszbm_area-CO",
    "password": "627uRmUfEVTCBIyS"
}

DELAYS = [1.2, 2.0, 2.8]

def _a_entero(v):
    if v is None: return None
    if isinstance(v, (int, float)): return int(v)
    d = re.sub(r"\D", "", str(v).split(",")[0] if re.search(r",\d{1,2}$", str(v)) and "." in str(v) else str(v))
    return int(d) if d else None

def _formato(v):
    n = _a_entero(v)
    return f"${n:,.0f}".replace(",", ".") if n is not None else None

def consultar_referencia(ref, project_filter=None):
    t0 = time.time()
    ref = str(ref).strip()
    
    empresas_a_probar = [e for e in EMPRESAS if str(e[0]) == str(project_filter)] if project_filter else EMPRESAS
    chrome_bin = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chrome_bin,
            headless=False,
            proxy=PROXY_CONFIG,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = browser.new_context(
            locale="es-CO",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()

        last_query_json = {}
        def _on_response(res):
            if "transaction/query" in res.url:
                try:
                    last_query_json["data"] = res.json()
                except Exception:
                    pass

        page.on("response", _on_response)

        try:
            page.goto(SITE, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#empresa", timeout=20000)

            for emp_id, emp_nombre in empresas_a_probar:
                page.select_option("#empresa", emp_id)

                try:
                    page.wait_for_selector("input#image1:not([disabled])", timeout=5000)
                except Exception:
                    pass

                page.evaluate("""() => {
                    const lbl = document.querySelector('label[for=image1]');
                    if (lbl) lbl.click();
                    const inp = document.querySelector('#image1');
                    if (inp) { inp.checked = true; inp.dispatchEvent(new Event('change', {bubbles: true})); }
                }""")

                page.click("#cuenta_contrato")
                page.keyboard.type(ref, delay=15)
                time.sleep(1.2)

                for n, espera in enumerate(DELAYS, 1):
                    if n > 1:
                        time.sleep(espera)

                    last_query_json.clear()

                    page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const cBtn = btns.find(b => b.innerText.includes('Consultar'));
                        if (cBtn) cBtn.click();
                    }""")

                    t_click = time.time()
                    aviso = None

                    while time.time() - t_click < 6.0:
                        # 1. Éxito con redirección a PSE
                        if "/pse/" in page.url or "/bancolombia/" in page.url:
                            try:
                                page.wait_for_function("/Valor/.test(document.body.innerText)", timeout=6000)
                                m = re.search(r"Valor\s*\$?\s*([\d.,]+)", page.inner_text("body"))
                                valor_str = m.group(1) if m else None
                            except Exception:
                                valor_str = None

                            browser.close()
                            return {
                                "ok": True,
                                "referencia": ref,
                                "valor": _a_entero(valor_str),
                                "texto": _formato(valor_str),
                                "empresa": emp_nombre,
                                "oficina": "PSE",
                                "mensaje": "OK",
                                "segundos": round(time.time() - t0, 2)
                            }

                        # 2. Alerta SweetAlert2
                        popup = page.locator(".swal2-popup")
                        if popup.count() and popup.first.is_visible():
                            titulo = page.locator(".swal2-title").first.inner_text().strip() if page.locator(".swal2-title").count() else ""
                            texto = page.locator(".swal2-html-container").first.inner_text().strip() if page.locator(".swal2-html-container").count() else ""
                            aviso = f"{titulo} {texto}".strip()

                            if page.locator(".swal2-confirm").count() and page.locator(".swal2-confirm").first.is_visible():
                                page.locator(".swal2-confirm").first.click()
                                time.sleep(0.3)
                            break
                        time.sleep(0.1)

                    aviso_lower = (aviso or "").lower()
                    query_result_str = str(last_query_json.get("data", {}).get("data", {}).get("Result", "")).lower()

                    # 3. Factura YA PAGADA (Saldo 0)
                    if any(w in aviso_lower for w in ["ya pagada", "sin saldo", "cuenta al día", "pagada"]) or "ya pagada" in query_result_str:
                        browser.close()
                        return {
                            "ok": False,
                            "referencia": ref,
                            "valor": 0,
                            "texto": "$0",
                            "empresa": emp_nombre,
                            "oficina": "PSE",
                            "mensaje": "REFERENCIA YA PAGADA (Sin saldo pendiente)",
                            "segundos": round(time.time() - t0, 2)
                        }

                    # 4. Factura no encontrada
                    if any(w in aviso_lower for w in ["no encontrada", "no existe", "no se encontraron facturas", "sin facturas"]) or "no encontrada" in query_result_str:
                        if project_filter:
                            browser.close()
                            return {
                                "ok": False,
                                "referencia": ref,
                                "valor": None,
                                "texto": None,
                                "mensaje": f"Referencia no encontrada en {emp_nombre}",
                                "segundos": round(time.time() - t0, 2)
                            }
                        break

                    # 5. reCAPTCHA "demasiado rápido" -> reintentar
                    if any(w in aviso_lower for w in ["rápido", "cookies", "intenta nuevamente"]):
                        continue

                page.click("#cuenta_contrato")
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")

            browser.close()
            return {
                "ok": False,
                "referencia": ref,
                "valor": None,
                "texto": None,
                "mensaje": "Referencia no encontrada en ninguna empresa",
                "segundos": round(time.time() - t0, 2)
            }

        except Exception as e:
            try: browser.close()
            except Exception: pass
            return {
                "ok": False,
                "referencia": ref,
                "valor": None,
                "texto": None,
                "mensaje": f"Error: {str(e)}",
                "segundos": round(time.time() - t0, 2)
            }
        finally:
            try: browser.close()
            except Exception: pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("referencia")
    parser.add_argument("--project", default=None)
    args = parser.parse_args()

    resultado = consultar_referencia(args.referencia, args.project)
    print(json.dumps(resultado, ensure_ascii=False))

if __name__ == "__main__":
    main()
