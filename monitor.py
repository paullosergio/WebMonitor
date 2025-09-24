import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime

import requests


class BettingMonitor:
    def __init__(self):
        # Configuration
        self.URL = "https://betesporte.bet.br/api/PreMatch/GetEvents?sportId=999&tournamentId=4200000001"
        self.INTERVAL = 30  # seconds between requests

        # Telegram configuration from environment variables
        self.BOT_TOKEN = os.getenv("BOT_TOKEN", "REMOVED_TOKEN")
        self.CHAT_ID = os.getenv("CHAT_ID", "6729439292")

        # Regex pattern for identifying events
        self.pattern = re.compile(r"para ter menos de (\d+(\.\d+)?) gols na partida", re.IGNORECASE)

        # File for storing notified events
        self.LOG_FILE = "notified_events.json"

        # Headers to simulate browser
        self.HEADERS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
        }

        # Runtime variables
        self.running = False
        self.thread = None
        self.notified_events = set()
        self.recent_logs = deque(maxlen=100)  # Keep last 100 log entries
        self.stats = {
            "start_time": None,
            "total_requests": 0,
            "successful_requests": 0,
            "events_found": 0,
            "notifications_sent": 0,
            "last_check": None,
            "last_error": None,
        }

        # Set up logging
        self.logger = logging.getLogger(__name__)

        # Load previously notified events
        self.load_notified_events()

    def load_notified_events(self):
        """Load previously notified events from file"""
        try:
            if os.path.exists(self.LOG_FILE):
                with open(self.LOG_FILE, "r") as f:
                    self.notified_events = set(json.load(f))
                self.log(f"Loaded {len(self.notified_events)} previously notified events")
            else:
                self.notified_events = set()
                self.log("No previous events file found, starting fresh")
        except Exception as e:
            self.log(f"Error loading notified events: {e}", level="ERROR")
            self.notified_events = set()

    def save_notified_events(self):
        """Save notified events to file"""
        try:
            with open(self.LOG_FILE, "w") as f:
                json.dump(list(self.notified_events), f)
        except Exception as e:
            self.log(f"Error saving notified events: {e}", level="ERROR")

    def send_telegram_message(self, message):
        """Send message to Telegram"""
        url = f"https://api.telegram.org/bot{self.BOT_TOKEN}/sendMessage"
        payload = {"chat_id": self.CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code == 200:
                self.stats["notifications_sent"] += 1
                self.log("Telegram notification sent successfully")
                return True
            else:
                self.log(f"Failed to send Telegram message: {response.status_code}", level="ERROR")
                return False
        except Exception as e:
            self.log(f"Error sending Telegram message: {e}", level="ERROR")
            return False

    def log(self, message, level="INFO"):
        """Add log entry with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = {"timestamp": timestamp, "level": level, "message": message}
        self.recent_logs.append(log_entry)

        # Also log to Python logger
        if level == "ERROR":
            self.logger.error(message)
        elif level == "WARNING":
            self.logger.warning(message)
        else:
            self.logger.info(message)

    def monitor_loop(self):
        """Main monitoring loop"""
        self.log("Starting betting monitor loop")
        self.stats["start_time"] = datetime.now()

        while self.running:
            try:
                self.stats["total_requests"] += 1
                self.stats["last_check"] = datetime.now()

                # Make API request directly (no proxy)
                response = self.make_api_request()

                if response.status_code != 200:
                    if response.status_code == 403:
                        error_msg = "Acesso negado (403) - possível bloqueio geográfico"
                        try:
                            response_text = response.text if hasattr(response, "text") else "No response text"
                            if "brazil" in response_text.lower() or "blocked" in response_text.lower():
                                error_msg = "Bloqueio geográfico ativo"
                        except:
                            pass
                    else:
                        error_msg = f"API falhou com status {response.status_code}"

                    self.log(error_msg, level="ERROR")
                    self.stats["last_error"] = error_msg
                    time.sleep(self.INTERVAL)
                    continue

                self.stats["successful_requests"] += 1

                # Parse JSON response
                try:
                    data = response.json()
                except Exception:
                    # Tenta alternativas: remover BOM, limpar prefixos, e fazer loads manual
                    try:
                        raw = response.content.decode("utf-8-sig", errors="ignore")
                        # Alguns endpoints podem começar com prefixos de proteção
                        for prefix in (")]}',\n", "\ufeff"):
                            if raw.startswith(prefix):
                                raw = raw[len(prefix) :]
                        data = json.loads(raw)
                    except Exception:
                        # Extra diagnóstico: content-type e amostra do corpo
                        try:
                            content_type = response.headers.get("Content-Type", "?")
                        except Exception:
                            content_type = "?"
                        preview = ""
                        try:
                            preview_text = response.text if hasattr(response, "text") else ""
                            preview = (preview_text or "")[:300]
                        except Exception:
                            preview = ""
                        self.log(
                            f"Response is not valid JSON (Content-Type={content_type}). Preview: {preview}...",
                            level="WARNING",
                        )
                        data = {"data": {"countries": [{"tournaments": [{"events": []}]}]}}

                # Extract events
                events = []
                try:
                    events = data.get("data", {}).get("countries", [])[0].get("tournaments", [])[0].get("events", [])
                except (IndexError, KeyError):
                    self.log("No events found in API response", level="WARNING")

                # Process events
                new_events_found = 0
                for event in events:
                    if self.process_event(event):
                        new_events_found += 1

                if new_events_found > 0:
                    self.log(f"Found {new_events_found} new matching events")
                    self.stats["events_found"] += new_events_found

                # Sleep before next check
                time.sleep(self.INTERVAL)

            except Exception as e:
                error_msg = f"Error in monitoring loop: {e}"
                self.log(error_msg, level="ERROR")
                self.stats["last_error"] = error_msg
                time.sleep(self.INTERVAL)

    def process_event(self, event):
        """Process a single event and send notification if needed"""
        try:
            home_team = event.get("homeTeamName", "")
            match = self.pattern.search(home_team)

            if match and event["id"] not in self.notified_events:
                gols_esperados = match.group(1)

                # Find the odd value
                odd_value = self.find_odd_value(event)

                # Convert date to local timezone
                try:
                    date_utc = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
                    date_local = date_utc.astimezone()
                    date_str = date_local.strftime("%d/%m/%Y %H:%M:%S")
                except:
                    date_str = event.get("date", "N/D")

                # Create message
                message = (
                    f"⚡ *Novo Evento Encontrado!*\n\n"
                    f"🏟️ Evento: {home_team}\n"
                    f"⏰ Data/Hora: {date_str}\n"
                    f"⚽ Menos de {gols_esperados} gols na partida\n"
                    f"💰 Odd: *{odd_value}*\n"
                    f"🆔 ID: {event['id']}"
                    f"🔗 Link:https://betesporte.bet.br/sports/desktop/pre-match-detail/999/4200000001/{event['id']}"
                )

                # Send notification
                if self.send_telegram_message(message):
                    self.notified_events.add(event["id"])
                    self.save_notified_events()
                    self.log(f"New event notification sent: {event['id']}")
                    return True
                else:
                    self.log(f"Failed to send notification for event: {event['id']}", level="ERROR")

            return False

        except Exception as e:
            self.log(f"Error processing event: {e}", level="ERROR")
            return False

    def make_api_request(self):
        """Make API request directly (no proxy)"""
        self.log("Tentando requisição direta...", level="INFO")
        response = self._make_direct_request()
        if response.status_code == 200:
            self.log("Sucesso com requisição direta!", level="INFO")
        else:
            self.log(f"Requisição direta retornou status {response.status_code}", level="WARNING")
        return response

    def _make_direct_request(self):
        """Make direct API request without proxy"""
        session = requests.Session()
        session.headers.update(self.HEADERS)
        session.headers.update({"Referer": "https://betesporte.bet.br/", "Origin": "https://betesporte.bet.br"})

        return session.get(self.URL, timeout=10)

    def find_odd_value(self, event):
        """Find the odd value for the matching market"""
        try:
            for market in event.get("markets", []):
                for option in market.get("options", []):
                    odd_value = option.get("odd")
                    if odd_value:
                        return odd_value
            return "N/D"
        except Exception:
            return "N/D"

    def start(self):
        """Start the monitoring thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
            self.thread.start()
            self.log("Betting monitor started")

    def stop(self):
        """Stop the monitoring thread"""
        if self.running:
            self.running = False
            self.log("Betting monitor stopped")

    def is_running(self):
        """Check if monitor is currently running"""
        return self.running

    def get_status(self):
        """Get current status information"""
        uptime = None
        if self.stats["start_time"]:
            uptime = str(datetime.now() - self.stats["start_time"]).split(".")[0]

        return {
            "running": self.running,
            "uptime": uptime,
            "stats": self.stats,
            "notified_events_count": len(self.notified_events),
            "config": {
                "interval": self.INTERVAL,
                "api_url": self.URL,
                "telegram_configured": bool(self.BOT_TOKEN and self.CHAT_ID),
            },
        }

    def get_recent_logs(self):
        """Get recent log entries"""
        return list(self.recent_logs)

    def test_api_connection(self):
        """Test API connection and return detailed status"""
        try:
            self.log("Testing API connection...", level="INFO")
            response = self.make_api_request()

            result = {
                "status_code": response.status_code,
                "success": response.status_code == 200,
                "timestamp": datetime.now().isoformat(),
                "message": "",
            }

            if response.status_code == 200:
                result["message"] = "API acessível e funcionando"
                try:
                    data = response.json()
                    events_count = len(
                        data.get("data", {}).get("countries", [])[0].get("tournaments", [])[0].get("events", [])
                    )
                    result["events_found"] = events_count
                    result["message"] += f" - {events_count} eventos encontrados"
                except:
                    result["message"] += " - Resposta válida mas estrutura inesperada"
            elif response.status_code == 403:
                result["message"] = "Acesso negado - possível bloqueio geográfico (Brasil apenas)"
                try:
                    response_text = response.text if hasattr(response, "text") else ""
                    if "brazil" in response_text.lower():
                        result["message"] = "Bloqueio confirmado: Acesso restrito ao Brasil"
                        result["blocked_region"] = True
                except:
                    pass
            else:
                result["message"] = f"Erro HTTP {response.status_code}"

            self.log(f"API test result: {result['message']}", level="INFO")
            return result

        except Exception as e:
            error_result = {
                "success": False,
                "status_code": 0,
                "message": f"Erro na conexão: {str(e)}",
                "timestamp": datetime.now().isoformat(),
            }
            self.log(f"API test failed: {str(e)}", level="ERROR")
            return error_result
