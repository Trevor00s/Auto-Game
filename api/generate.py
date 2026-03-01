from http.server import BaseHTTPRequestHandler
import json
import os
import re
import traceback
import time


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length  = int(self.headers.get('Content-Length', 0))
            raw     = self.rfile.read(length)
            body    = json.loads(raw)
            prompt  = body.get("prompt", "").strip()

            if not prompt:
                return self._json({"error": "prompt is required"}, 400)

            private_key = os.environ.get("OG_PRIVATE_KEY", "")
            if not private_key:
                return self._json({"error": "OG_PRIVATE_KEY not set"}, 500)

            try:
                import opengradient as og
            except ImportError as e:
                return self._json({"error": f"opengradient not installed: {e}"}, 500)

            try:
                og.init(private_key=private_key)
            except Exception as e:
                return self._json({"error": f"og.init() failed: {e}"}, 500)

            # Retry up to 3 times on connection errors
            result = None
            last_error = None
            for attempt in range(3):
                try:
                    result = og.global_client.llm.chat(
                        model=og.TEE_LLM.GPT_4O,
                        messages=[
                            {
                                "role": "user",
                                "content": (
                                    f"Create a complete browser game: {prompt}\n\n"
                                    "IMPORTANT: Output ONLY raw HTML. "
                                    "Start EXACTLY with <!DOCTYPE html> — no markdown, "
                                    "no backticks, no explanation."
                                )
                            }
                        ],
                        max_tokens=4000,
                        temperature=0.7,
                        x402_settlement_mode=og.x402SettlementMode.SETTLE_BATCH
                    )
                    break  # success
                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    # Retry only on connection errors
                    if any(x in err_str for x in ["connection", "reset", "backend", "aborted"]):
                        if attempt < 2:
                            time.sleep(2 * (attempt + 1))  # 2s, 4s
                            continue
                    break  # non-retryable error

            if result is None:
                return self._json({"error": f"llm.chat() failed after 3 attempts: {last_error}"}, 500)

            # Extract HTML — TextGenerationOutput.chat_output['content']
            html = ""
            try:
                if hasattr(result, "chat_output") and isinstance(result.chat_output, dict):
                    html = result.chat_output.get("content", "")
                elif hasattr(result, "choices") and result.choices:
                    html = result.choices[0].message.content
                elif hasattr(result, "content") and isinstance(result.content, str):
                    html = result.content
                elif isinstance(result, str):
                    html = result
                else:
                    html = str(result)
            except Exception as e:
                return self._json({"error": f"Content extraction failed: {e}"}, 500)

            if not html:
                return self._json({"error": "Empty response from model"}, 500)

            # Strip markdown fences if present
            html = re.sub(r"^```(?:html)?\s*", "", html.strip(), flags=re.IGNORECASE)
            html = re.sub(r"\s*```\s*$", "", html.strip())
            html = html.strip()

            if len(html) < 100:
                return self._json({"error": f"Response too short: {html[:200]}"}, 500)

            if not (html.lower().startswith("<!doctype") or html.lower().startswith("<html")):
                return self._json({"error": f"Not valid HTML. Got: {html[:200]}"}, 500)

            return self._json({"html": html, "size": len(html)})

        except Exception as e:
            return self._json({"error": str(e), "traceback": traceback.format_exc()}, 500)

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
