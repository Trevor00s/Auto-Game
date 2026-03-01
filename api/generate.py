from http.server import BaseHTTPRequestHandler
import json
import os
import re
import traceback
import time
import urllib.request


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length  = int(self.headers.get('Content-Length', 0))
            body    = json.loads(self.rfile.read(length))
            prompt  = body.get("prompt", "").strip()

            if not prompt:
                return self._json({"error": "prompt is required"}, 400)

            html, source = self._generate(prompt)

            if not html:
                return self._json({"error": "Empty response from all providers"}, 500)

            return self._json({"html": html, "size": len(html), "source": source})

        except Exception as e:
            return self._json({"error": str(e), "traceback": traceback.format_exc()}, 500)

    def _generate(self, prompt):
        og_key  = os.environ.get("OG_PRIVATE_KEY", "")
        ant_key = os.environ.get("ANTHROPIC_API_KEY", "")

        system = (
            "You are an elite game developer AI. Generate complete, playable browser games "
            "as a SINGLE self-contained HTML file. Output ONLY raw HTML starting with "
            "<!DOCTYPE html>. No markdown, no backticks, no explanation."
        )
        user_msg = (
            f"Create a complete browser game: {prompt}\n\n"
            "Requirements: title screen, score display, game over + replay button, "
            "keyboard and mouse controls, sound via Web Audio API, 60fps target. "
            "Output ONLY the raw HTML file."
        )

        # Try OpenGradient first
        if og_key:
            try:
                html = self._call_opengradient(og_key, user_msg)
                if html:
                    return html, "opengradient"
            except Exception as e:
                print(f"OpenGradient failed: {e}")

        # Fallback: Anthropic API
        if ant_key:
            try:
                html = self._call_anthropic(ant_key, system, user_msg)
                if html:
                    return html, "anthropic"
            except Exception as e:
                print(f"Anthropic failed: {e}")

        return None, None

    def _call_opengradient(self, private_key, user_msg):
        import opengradient as og
        og.init(private_key=private_key)

        for attempt in range(3):
            try:
                result = og.global_client.llm.chat(
                    model=og.TEE_LLM.GPT_4O,
                    messages=[{"role": "user", "content": user_msg}],
                    max_tokens=4000,
                    temperature=0.7,
                    x402_settlement_mode=og.x402SettlementMode.SETTLE_BATCH
                )
                html = ""
                if hasattr(result, "chat_output") and isinstance(result.chat_output, dict):
                    html = result.chat_output.get("content", "")
                elif hasattr(result, "choices") and result.choices:
                    html = result.choices[0].message.content
                else:
                    html = str(result)
                return self._clean_html(html)
            except Exception as e:
                if attempt < 2 and any(x in str(e).lower() for x in ["connection", "reset", "backend"]):
                    time.sleep(2 * (attempt + 1))
                    continue
                raise

    def _call_anthropic(self, api_key, system, user_msg):
        payload = json.dumps({
            "model": "claude-opus-4-6",
            "max_tokens": 8000,
            "system": system,
            "messages": [{"role": "user", "content": user_msg}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=55) as res:
            data = json.loads(res.read())

        html = data["content"][0]["text"]
        return self._clean_html(html)

    def _clean_html(self, html):
        if not html:
            return ""
        html = re.sub(r"^```(?:html)?\s*", "", html.strip(), flags=re.IGNORECASE)
        html = re.sub(r"\s*```\s*$", "", html.strip())
        html = html.strip()
        if len(html) < 100:
            return ""
        if not (html.lower().startswith("<!doctype") or html.lower().startswith("<html")):
            return ""
        return html

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
