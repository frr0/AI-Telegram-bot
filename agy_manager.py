import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


def _run_agy_print(arg: str, timeout: int = 10) -> Tuple[int, str]:
    """Runs `agy --print <arg>` and returns (exit_code, stdout)."""
    agy_cmd = shutil.which("agy") or "agy"
    try:
        res = subprocess.run(
            [agy_cmd, "--print", arg],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return res.returncode, res.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, "⏳ Timeout durante il recupero delle informazioni da Agy."
    except Exception as e:
        return 1, f"❌ Errore durante l'esecuzione di agy: {e}"


def get_usage_report() -> str:
    """Parses and formats model quota usage from `agy --print /usage`."""
    code, out = _run_agy_print("/usage", timeout=12)
    if code != 0 or not out:
        return f"❌ Impossibile recuperare le quote di utilizzo:\n{out}"

    lines = [l.strip() for l in out.split("\n") if l.strip()]
    if not lines or "Quota:" in lines[0] and len(lines) == 1:
        return "📊 *Quote Utilizzo Modelli Agy*\nNessun dato di quota disponibile."

    # Parse TSV or whitespace separated lines
    # Typical line: Gemini Models \t Weekly Limit Remaining \t 98% \t 2026-11-11T12:42:02Z
    formatted_entries = []
    for line in lines:
        if line.lower().startswith("quota:"):
            continue
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) < 3:
            parts = re.split(r"\s{2,}", line)

        if len(parts) >= 3:
            model_group = parts[0]
            limit_type = parts[1]
            percent = parts[2]
            reset_time = parts[3] if len(parts) > 3 else ""

            # Format reset date/time nicely if ISO format
            reset_clean = reset_time
            if "T" in reset_time and "Z" in reset_time:
                try:
                    dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
                    reset_clean = dt.strftime("%d/%m %H:%M UTC")
                except Exception:
                    reset_clean = reset_time

            reset_suffix = f" _(reset: {reset_clean})_" if reset_clean else ""
            formatted_entries.append(
                f"• *{model_group}* — {limit_type}\n  └ Residuo: *{percent}*{reset_suffix}"
            )
        else:
            formatted_entries.append(f"• `{line}`")

    body = "\n".join(formatted_entries) if formatted_entries else f"```\n{out}\n```"
    return f"📊 *Quote Utilizzo Modelli Antigravity (Agy)*\n\n{body}"


def get_credits_report() -> str:
    """Parses and formats G1 credits from `agy --print /credits`."""
    code, out = _run_agy_print("/credits", timeout=10)
    if code != 0 or not out:
        return f"❌ Impossibile recuperare i crediti:\n{out}"

    lines = [l.strip() for l in out.split("\n") if l.strip()]
    rem_credits = "N/A"
    upgrade_url = "https://antigravity.google/g1-upgrade"

    for l in lines:
        if "remaining credits" in l.lower():
            parts = l.split()
            if len(parts) >= 3:
                rem_credits = parts[-1]
        elif "upgrade" in l.lower() or "http" in l.lower():
            parts = l.split()
            for p in parts:
                if p.startswith("http"):
                    upgrade_url = p

    return (
        "💳 *Crediti Antigravity G1*\n\n"
        f"• *Crediti Residui:* `{rem_credits}`\n"
        f"• *Link Ricarica / Upgrade:* [antigravity.google/g1-upgrade]({upgrade_url})\n\n"
        "_I crediti consentono l'uso esteso dei modelli più potenti oltre la quota settimanale._"
    )


def get_mcp_report() -> str:
    """Parses and formats active MCP servers from `agy mcp list`."""
    agy_cmd = shutil.which("agy") or "agy"
    try:
        res = subprocess.run(
            [agy_cmd, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=10
        )
        out = res.stdout.strip()
    except Exception as e:
        return f"❌ Errore esecuzione agy mcp list: {e}"

    if not out:
        return "🔌 *Server MCP*\nNessun server MCP configurato."

    lines = [l for l in out.split("\n") if l.strip()]
    if len(lines) <= 1:
        return f"🔌 *Server MCP Configurate*\n```\n{out}\n```"

    header = lines[0]
    servers = []
    for l in lines[1:]:
        parts = l.split()
        if len(parts) >= 4:
            name = parts[0]
            stype = parts[1]
            status = parts[2]
            cmd = " ".join(parts[3:])
            status_icon = "🟢" if status.lower() in ["enabled", "active"] else "⚪"
            servers.append(
                f"{status_icon} *{name}* ({stype})\n  └ Stato: `{status}` | Comando: `{cmd}`"
            )
        else:
            servers.append(f"• `{l}`")

    body = "\n\n".join(servers)
    return f"🔌 *Server MCP Attivi in Agy ({len(servers)})*\n\n{body}"


def get_skills_report() -> str:
    """Parses and formats installed skills from `agy --print /skills`."""
    code, out = _run_agy_print("/skills", timeout=12)
    if code != 0 or not out:
        return f"❌ Impossibile recuperare le skills:\n{out}"

    lines = [l.strip() for l in out.split("\n") if l.strip()]
    skills = []
    for l in lines:
        if l.lower().startswith("available skills:"):
            continue
        parts = l.split("\t") if "\t" in l else re.split(r"\s{2,}", l)
        if len(parts) >= 2:
            name = parts[0].strip()
            desc = parts[1].strip()
            skills.append(f"⚡ *{name}*\n  └ _{desc}_")
        elif l:
            skills.append(f"• `{l}`")

    body = "\n\n".join(skills) if skills else f"```\n{out}\n```"
    return f"⚡ *Skill Antigravity Disponibili ({len(skills)})*\n\n{body}"


def get_agents_report() -> str:
    """Parses and formats custom agents from `agy --print /agents`."""
    code, out = _run_agy_print("/agents", timeout=10)
    if code != 0:
        return f"❌ Impossibile recuperare la lista agenti:\n{out}"

    lines = [l.strip() for l in out.split("\n") if l.strip()]
    clean_lines = [l for l in lines if not l.lower().startswith("available agents:")]

    if not clean_lines:
        return (
            "🤖 *Agenti Custom Antigravity*\n\n"
            "Attualmente sono attivi i sub-agenti built-in di sistema:\n"
            "• *self*: Subagente clone con contesto isolato\n"
            "• *research*: Agente di sola lettura e ricerca veloce\n"
            "• *image-generator*: Generatore e ritoccatore di immagini\n\n"
            "_Puoi definire nuovi agenti personalizzati in qualsiasi momento con il comando `define_subagent`._"
        )

    body = "\n".join([f"• `{l}`" for l in clean_lines])
    return f"🤖 *Agenti Disponibili*\n\n{body}"


def get_effort_report() -> str:
    """Parses current effort level from `agy --print /effort`."""
    code, out = _run_agy_print("/effort", timeout=10)
    lines = [l.strip() for l in out.split("\n") if l.strip()]
    val = lines[-1] if lines else "medium"
    return (
        f"⚙️ *Livello Reasoning Effort (Pensiero Profondo)*\n\n"
        f"Livello attuale: *{val.upper()}*\n\n"
        "Livelli supportati:\n"
        "• *low*: Risposte rapidissime a basso consumo di quota\n"
        "• *medium*: Bilanciamento standard ottimale (default)\n"
        "• *high*: Ragionamento esteso per bug complessi o refactor\n"
        "• *max*: Massima profondità di analisi per sfide architetturali"
    )


def get_workflow_help(mode: str) -> str:
    """Returns details and quick-action advice for a specific slash command workflow."""
    workflows = {
        "teamwork": (
            "👥 *Modalità /teamwork (Team Autonomo)*\n\n"
            "Orchestra un team multi-agente in parallelo con compiti indipendenti, "
            "ideale per grandi progetti, refactoring ampi e indagini complesse.\n\n"
            "💡 *Come usarlo:*\n"
            "Invia un messaggio che inizia con `/teamwork`, ad esempio:\n"
            "`/teamwork Riorganizza i test unitari e crea una suite automatizzata`"
        ),
        "goal": (
            "🎯 *Modalità /goal (Perseveranza Long-Running)*\n\n"
            "Modalità notturna o a lungo termine: l'agente lavora autonomamente "
            "in modo iper-approfondito senza fermarsi finché l'obiettivo non è completamente raggiunto e validato.\n\n"
            "💡 *Come usarlo:*\n"
            "Invia un messaggio che inizia con `/goal`, ad esempio:\n"
            "`/goal Risolvi tutti gli errori di linting e fai passare il 100% dei test`"
        ),
        "plan": (
            "📝 *Modalità /plan (Pianificazione Architetturale)*\n\n"
            "L'agente crea prima un piano formale passo-passo prima di toccare qualsiasi riga di codice, "
            "permettendoti di approvare la strategia.\n\n"
            "💡 *Come usarlo:*\n"
            "Invia un messaggio che inizia con `/plan`, ad esempio:\n"
            "`/plan Aggiungi un sistema di autenticazione JWT su FastAPI`"
        ),
        "boost": (
            "🚀 *Modalità /boost (Massima Accuratezza)*\n\n"
            "Attiva deep thinking, pianificazione strategica, molteplici prospettive "
            "e verifica rigorosa prima di consegnare il risultato.\n\n"
            "💡 *Come usarlo:*\n"
            "`/boost Ottimizza le prestazioni della query SQL critica`"
        ),
        "browser": (
            "🌐 *Modalità /browser (Navigazione Web & E2E)*\n\n"
            "Consente all'agente di interagire con il browser, fare screenshot, navigare pagine web, "
            "compilare form e testare applicazioni web via Playwright.\n\n"
            "💡 *Come usarlo:*\n"
            "`/browser Apri http://localhost:3000 e verifica il flusso di login`"
        ),
        "compact": (
            "🗜️ *Modalità /compact (Compattazione Contesto)*\n\n"
            "Riassume la cronologia della conversazione attuale per liberare spazio "
            "nella finestra di contesto (token) preservando le decisioni chiave.\n\n"
            "💡 *Come usarlo:*\n"
            "Basta digitare `/compact` o premere il pulsante rapido."
        ),
        "grill_me": (
            "🧑‍🏫 *Modalità /grill-me (Intervista Interattiva)*\n\n"
            "L'agente ti sottoporrà a un'intervista con domande mirate a risposta multipla "
            "per sciogliere qualsiasi dubbio architetturale o di design prima di iniziare.\n\n"
            "💡 *Come usarlo:*\n"
            "`/grill-me Voglio creare un sistema di notifiche push per utenti mobili`"
        ),
        "learn": (
            "💡 *Modalità /learn (Persistenza Regole)*\n\n"
            "Insegna all'agente una preferenza o una soluzione a un problema complesso "
            "affinché la ricordi e la applichi automaticamente in tutti i task futuri.\n\n"
            "💡 *Come usarlo:*\n"
            "`/learn Usa sempre ruff per formattare il codice Python invece di flake8`"
        ),
    }
    return workflows.get(mode, "ℹ️ *Opzione Agy:* Modalità selezionata.")
