"""Envoi d'alertes mail via Gmail SMTP (TLS).

Anti-spam : si un envoi a eu lieu il y a moins de `ALERT_MIN_INTERVAL_SECONDS`,
les nouvelles transitions sont mises en file et envoyées au prochain cycle
quand l'intervalle est écoulé.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from webapp.settings import Settings
from webapp.state import Transition

log = logging.getLogger(__name__)


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._last_sent_at: float = 0.0
        self._pending: list[Transition] = []

    # ------------------------------------------------------------------ public
    def queue(self, transitions: list[Transition]) -> None:
        """Ajoute des transitions à la file. Envoie immédiatement si possible."""
        if not transitions:
            return
        with self._lock:
            self._pending.extend(transitions)
        self.flush()

    def flush(self) -> None:
        """Envoie les transitions en attente si l'intervalle anti-spam est écoulé."""
        if not self.settings.mail_enabled:
            return

        with self._lock:
            if not self._pending:
                return
            now = time.time()
            if now - self._last_sent_at < self.settings.alert_min_interval:
                return
            batch = list(self._pending)
            self._pending.clear()
            self._last_sent_at = now

        try:
            self._send(batch)
        except Exception as exc:
            log.error("Échec envoi mail : %s", exc)
            # On remet les transitions dans la file pour réessayer au prochain cycle.
            with self._lock:
                self._pending = batch + self._pending

    # ------------------------------------------------------------------ private
    def _send(self, transitions: list[Transition]) -> None:
        subject = self._build_subject(transitions)
        html = self._build_html(transitions)
        text = self._build_text(transitions)

        if self.settings.alert_dry_run:
            log.warning("[DRY-RUN] Mail (%d transitions) — sujet: %s",
                        len(transitions), subject)
            log.info("[DRY-RUN] corps texte:\n%s", text)
            return

        msg = MIMEMultipart("alternative")
        msg["From"] = self.settings.smtp_user
        msg["To"] = ", ".join(self.settings.alert_to)
        msg["Subject"] = subject
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as srv:
            srv.starttls(context=context)
            srv.login(self.settings.smtp_user, self.settings.smtp_password)
            srv.send_message(msg)

        log.info("Mail envoyé à %s (%d transitions)",
                 ", ".join(self.settings.alert_to), len(transitions))

    @staticmethod
    def _build_subject(transitions: list[Transition]) -> str:
        if len(transitions) == 1:
            t = transitions[0]
            return f"[Crypto Alert] {t.symbol} {t.horizon}: {t.previous_action} → {t.new_action}"
        buys = sum(1 for t in transitions if t.new_action == "BUY")
        sells = sum(1 for t in transitions if t.new_action == "SELL")
        holds = sum(1 for t in transitions if t.new_action == "HOLD")
        parts = []
        if buys:  parts.append(f"{buys} BUY")
        if sells: parts.append(f"{sells} SELL")
        if holds: parts.append(f"{holds} HOLD")
        return f"[Crypto Alert] {len(transitions)} transitions : " + ", ".join(parts)

    @staticmethod
    def _fmt_price(v: float | None) -> str:
        if v is None:
            return "—"
        if abs(v) >= 1000:
            return f"{v:,.2f}"
        if abs(v) >= 1:
            return f"{v:.4f}"
        return f"{v:.6f}"

    @staticmethod
    def _action_color(action: str) -> str:
        return {"BUY": "#16a34a", "SELL": "#dc2626", "HOLD": "#ca8a04"}.get(action, "#666")

    def _build_text(self, transitions: list[Transition]) -> str:
        lines = ["Transitions détectées sur le marché crypto :\n"]
        for t in transitions:
            lines.append(
                f"• {t.symbol} ({t.horizon}) : {t.previous_action} → {t.new_action}\n"
                f"  score={t.score:+.2f}  confidence={t.confidence:.2f}\n"
                f"  entry={self._fmt_price(t.entry)}  "
                f"SL={self._fmt_price(t.stop_loss)}  TP={self._fmt_price(t.take_profit)}\n"
                f"  Raisons : {'; '.join(t.reasons[:3])}\n"
            )
        lines.append("\n— Crypto analysis scripts")
        return "\n".join(lines)

    def _build_html(self, transitions: list[Transition]) -> str:
        rows = []
        for t in transitions:
            color = self._action_color(t.new_action)
            reasons = "".join(f"<li>{r}</li>" for r in t.reasons[:4])
            rows.append(f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #eee"><strong>{t.symbol}</strong></td>
              <td style="padding:8px;border-bottom:1px solid #eee">{t.horizon}</td>
              <td style="padding:8px;border-bottom:1px solid #eee">
                <span style="color:#888">{t.previous_action}</span> →
                <span style="color:{color};font-weight:bold">{t.new_action}</span>
              </td>
              <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{t.score:+.2f}</td>
              <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{t.confidence:.2f}</td>
              <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{self._fmt_price(t.entry)}</td>
              <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{self._fmt_price(t.stop_loss)}</td>
              <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{self._fmt_price(t.take_profit)}</td>
              <td style="padding:8px;border-bottom:1px solid #eee"><ul style="margin:0;padding-left:18px">{reasons}</ul></td>
            </tr>""")

        return f"""<html><body style="font-family:system-ui,sans-serif;background:#fafafa;padding:16px">
          <h2 style="margin:0 0 12px">🔔 {len(transitions)} transition(s) détectée(s)</h2>
          <table style="border-collapse:collapse;background:white;width:100%;max-width:1000px">
            <thead>
              <tr style="background:#f3f4f6;text-align:left">
                <th style="padding:8px">Symbole</th>
                <th style="padding:8px">Horizon</th>
                <th style="padding:8px">Signal</th>
                <th style="padding:8px;text-align:right">Score</th>
                <th style="padding:8px;text-align:right">Conf.</th>
                <th style="padding:8px;text-align:right">Entry</th>
                <th style="padding:8px;text-align:right">SL</th>
                <th style="padding:8px;text-align:right">TP</th>
                <th style="padding:8px">Raisons</th>
              </tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
          <p style="color:#888;font-size:12px;margin-top:16px">
            Signaux purement techniques. Ce n'est pas un conseil financier.
          </p>
        </body></html>"""
