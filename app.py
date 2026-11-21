#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Vanti - Servidor FastAPI ultra rápido con Worker Browser Residente
Velocidad récord (~3s) con Chromium caliente en memoria y calibración de reCAPTCHA Enterprise
"""
import asyncio
import os
import random
import re
import time
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

SITE = "https://pagosenlinea.grupovanti.com/"

EMPRESAS = {
    "79": "Vanti S.A. E.S.P.",
    "80": "Gas Natural Cundiboyacense",
    "81": "Gas Natural del Cesar (Nacer)",
    "82": "Gas Natural del Oriente",
    "84": "Vanti Soluciones"
}

def get_proxy_config():
    session_id = f"vanti{random.randint(100000, 999999)}"
    return {
        "server": "http://proxy.smartproxy.net:3120",
        "username": f"smart-zxr9160zszbm_area-CO_session-{session_id}_lifetime-30m",
        "password": "627uRmUfEVTCBIyS"
    }

def _a_entero(v):
    if v is None: return None
    if isinstance(v, (int, float)): return int(v)
    d = re.sub(r"\D", "", str(v).split(",")[0] if re.search(r",\d{1,2}$", str(v)) and "." in str(v) else str(v))
    return int(d) if d else None

def _formato(v):
    n = _a_entero(v)
    return f"${n:,.0f}".replace(",", ".") if n is not None else None

class VantiBrowserWorker:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.lock = asyncio.Lock()
        self.last_query_res = {}
        self.is_ready = False

    async def start(self):
        await self.close()
        chrome_bin = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            executable_path=chrome_bin,
            headless=False,
            proxy=get_proxy_config(),
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        self.context = await self.browser.new_context(
            locale="es-CO",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        await self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        await self.load_page()

    async def load_page(self):
        self.is_ready = False
        if self.page and not self.page.is_closed():
            try: await self.page.close()
            except Exception: pass

        self.page = await self.context.new_page()

        def _on_response(res):
            if "transaction/query" in res.url:
                try:
                    asyncio.create_task(self._capture_resp(res))
                except Exception:
                    pass

        self.page.on("response", _on_response)

        for attempt in range(1, 4):
            try:
                await self.page.goto(SITE, wait_until="domcontentloaded", timeout=25000)
                await self.page.wait_for_selector("#empresa", timeout=15000)
                self.is_ready = True
                print(f"[Worker] Página inicial lista (intento {attempt})")
                return
            except Exception as e:
                print(f"[Worker] Intento {attempt} cargando portada: {e}")
                if attempt == 3:
                    raise
                await asyncio.sleep(2)

    async def _capture_resp(self, res):
        try:
            self.last_query_res = await res.json()
        except Exception:
            pass

    async def ensure_ready(self):
        if not self.is_ready or not self.page or self.page.is_closed():
            await self.start()

    async def query(self, ref: str, project: Optional[str] = None):
        async with self.lock:
            await self.ensure_ready()
            t0 = time.time()
            ref = str(ref).strip()
            
            empresas_lista = [str(project).strip()] if project else list(EMPRESAS.keys())

            for p_id in empresas_lista:
                emp_nombre = EMPRESAS.get(p_id, "Vanti S.A. E.S.P.")

                # Si la página navegó fuera de la portada (ej: a PSE) o se cerró, recargar
                if not self.page or self.page.is_closed() or not self.page.url.startswith(SITE.rstrip("/")):
                    await self.load_page()

                page = self.page
                self.last_query_res = {}

                # 1. Seleccionar empresa y medio PSE
                await page.select_option("#empresa", p_id)
                await page.evaluate('''() => {
                    const lbl = document.querySelector('label[for=image1]');
                    if (lbl) lbl.click();
                    const inp = document.querySelector('#image1');
                    if (inp) { inp.checked = true; inp.dispatchEvent(new Event('change', {bubbles: true})); }
                }''')

                # 2. Escribir referencia
                await page.click("#cuenta_contrato")
                await page.fill("#cuenta_contrato", "")
                await page.keyboard.type(ref, delay=15)

                # 3. Pausa estratégica de 1.2s para reCAPTCHA Enterprise
                await asyncio.sleep(1.2)

                for intento in range(1, 4):
                    if intento > 1:
                        await asyncio.sleep(1.8)

                    # Clic vía JS nativo para no ser bloqueado por spinners/overlays
                    await page.evaluate('''() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const cBtn = btns.find(b => b.innerText.includes('Consultar'));
                        if (cBtn) cBtn.click();
                    }''')

                    t_click = time.time()
                    aviso = None

                    while time.time() - t_click < 6.0:
                        # Caso A: Redirección exitosa a pasarela PSE / Bancolombia
                        if "/pse/" in page.url or "/bancolombia/" in page.url:
                            try:
                                await page.wait_for_function("/Valor/.test(document.body.innerText)", timeout=6000)
                                body_text = await page.inner_text("body")
                                m = re.search(r"Valor\s*\$?\s*([\d.,]+)", body_text)
                                valor_str = m.group(1) if m else None
                            except Exception:
                                valor_str = None

                            # Recarga en segundo plano para dejar el worker listo para la próxima consulta
                            asyncio.create_task(self.load_page())

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

                        # Caso B: Popup SweetAlert2
                        popup = page.locator(".swal2-popup")
                        if await popup.count() and await popup.first.is_visible():
                            titulo = await page.locator(".swal2-title").first.inner_text() if await page.locator(".swal2-title").count() else ""
                            texto = await page.locator(".swal2-html-container").first.inner_text() if await page.locator(".swal2-html-container").count() else ""
                            aviso = f"{titulo} {texto}".strip()

                            confirm = page.locator(".swal2-confirm")
                            if await confirm.count() and await confirm.first.is_visible():
                                await confirm.first.click()
                                await asyncio.sleep(0.3)
                            break

                        await asyncio.sleep(0.1)

                    aviso_lower = (aviso or "").lower()
                    query_result_str = str(self.last_query_res.get("data", {}).get("Result", "")).lower()
                    print(f"[Query] ref={ref} intento={intento} aviso='{aviso}' query_result_str='{query_result_str}'", flush=True)

                    # Caso C: Factura YA PAGADA (Saldo 0)
                    if any(w in aviso_lower for w in ["ya pagada", "sin saldo", "cuenta al día", "pagada"]) or "ya pagada" in query_result_str:
                        await page.click("#cuenta_contrato")
                        await page.fill("#cuenta_contrato", "")
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

                    # Caso D: Factura no encontrada en esta empresa
                    if any(w in aviso_lower for w in ["no encontrada", "no existe", "no se encontraron facturas", "sin facturas"]) or "no encontrada" in query_result_str:
                        if project:
                            await page.click("#cuenta_contrato")
                            await page.fill("#cuenta_contrato", "")
                            return {
                                "ok": False,
                                "referencia": ref,
                                "valor": None,
                                "texto": None,
                                "mensaje": f"Referencia no encontrada en {emp_nombre}",
                                "segundos": round(time.time() - t0, 2)
                            }
                        else:
                            # Probar con la siguiente empresa
                            break

                    # Caso E: reCAPTCHA "demasiado rápido" o cookies -> reintentar
                    if any(w in aviso_lower for w in ["rápido", "cookies", "intenta nuevamente"]):
                        continue

            return {
                "ok": False,
                "referencia": ref,
                "valor": None,
                "texto": None,
                "mensaje": "Referencia no encontrada en ninguna empresa",
                "segundos": round(time.time() - t0, 2)
            }

    async def close(self):
        self.is_ready = False
        try:
            if self.browser: await self.browser.close()
            if self.playwright: await self.playwright.stop()
        except Exception:
            pass

worker = VantiBrowserWorker()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicia Chromium caliente en memoria en segundo plano
    asyncio.create_task(worker.start())
    yield
    await worker.close()

app = FastAPI(title="API Facturación Vanti", version="3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {
        "servicio": "API Facturación Vanti",
        "version": "3.0 Ultra-Fast",
        "estado": "activo",
        "worker_listo": worker.is_ready,
        "uso": "/api/vanti?ref=NUMERO_FACTURA&project=ID_EMPRESA",
        "ejemplo": "/api/vanti?ref=60694487&project=79"
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "worker_ready": worker.is_ready
    }

@app.get("/api/vanti")
async def consultar_vanti(
    ref: str = Query(..., description="Cuenta contrato o referencia de pago"),
    project: Optional[str] = Query(None, description="ID Empresa (79, 80, 81, 82, 84)")
):
    ref_clean = ref.strip()
    if not ref_clean.isdigit() or len(ref_clean) < 6:
        raise HTTPException(status_code=400, detail="Referencia inválida (debe ser numérico de al menos 6 dígitos)")

    # Si aún no está listo el worker, esperar hasta 10s a que inicialice
    if not worker.is_ready:
        for _ in range(20):
            if worker.is_ready:
                break
            await asyncio.sleep(0.5)

    try:
        resultado = await asyncio.wait_for(worker.query(ref_clean, project), timeout=45.0)
        return resultado
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "referencia": ref_clean,
            "mensaje": "Timeout esperando respuesta de Vanti (>45s)"
        }
    except Exception as e:
        return {
            "ok": False,
            "referencia": ref_clean,
            "mensaje": f"Error de servidor: {str(e)}"
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8088, reload=False)
