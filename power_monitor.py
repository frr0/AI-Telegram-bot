import os
import subprocess
from datetime import datetime
from typing import Dict, Any, Tuple

DEFAULT_KWH_COST = 0.28       # Costo medio energia in Italia (€/kWh)
DEFAULT_PC_AVG_WATTS = 18.0   # Consumo medio stimato ThinkPad L14 Gen 2a (AMD Ryzen 5 PRO 5650U)
DEFAULT_RPI_AVG_WATTS = 0.9   # Consumo medio stimato Raspberry Pi Zero W Rev 1.1 (~0.8W - 1.1W)
RPI_SSH_HOSTS = ["rpi@raspberrypi.local", "rpi@100.81.217.118"]


def get_battery_info() -> Dict[str, Any]:
    """Retrieves local laptop battery and AC status."""
    info: Dict[str, Any] = {
        "ac_online": None,
        "status": "Sconosciuto",
        "capacity": None,
        "power_w": None
    }
    bat_path = "/sys/class/power_supply/BAT0"
    ac_path = "/sys/class/power_supply/AC"

    if os.path.exists(ac_path):
        try:
            with open(os.path.join(ac_path, "online")) as f:
                info["ac_online"] = (f.read().strip() == "1")
        except Exception:
            pass

    if os.path.exists(bat_path):
        try:
            with open(os.path.join(bat_path, "status")) as f:
                info["status"] = f.read().strip()
            with open(os.path.join(bat_path, "capacity")) as f:
                info["capacity"] = int(f.read().strip())
        except Exception:
            pass

        power_w = None
        if os.path.exists(os.path.join(bat_path, "power_now")):
            try:
                with open(os.path.join(bat_path, "power_now")) as f:
                    power_w = int(f.read().strip()) / 1e6
            except Exception:
                pass
        elif os.path.exists(os.path.join(bat_path, "current_now")) and os.path.exists(os.path.join(bat_path, "voltage_now")):
            try:
                with open(os.path.join(bat_path, "current_now")) as fc, open(os.path.join(bat_path, "voltage_now")) as fv:
                    c = int(fc.read().strip())
                    v = int(fv.read().strip())
                    power_w = (c * v) / 1e12
            except Exception:
                pass
        info["power_w"] = power_w

    return info


def get_pc_uptime_seconds() -> Tuple[float, float]:
    """
    Returns (seconds_today, seconds_month) for the PC by parsing `last -F -x reboot`.
    Falls back to /proc/uptime if last is unavailable.
    """
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    month_start = datetime(now.year, now.month, 1, 0, 0, 0)

    sessions = []
    try:
        out = subprocess.check_output(["last", "-F", "-x", "reboot"], text=True, stderr=subprocess.DEVNULL)
        for line in out.strip().split("\n"):
            if not line.startswith("reboot"):
                continue
            parts = line.split()
            try:
                idx_year = -1
                for i, p in enumerate(parts):
                    if p.isdigit() and len(p) == 4 and int(p) >= 2020:
                        idx_year = i
                        break
                if idx_year != -1:
                    start_str = " ".join(parts[idx_year - 3 : idx_year + 1])
                    start_dt = datetime.strptime(start_str, "%b %d %H:%M:%S %Y")

                    if "still running" in line:
                        end_dt = now
                    else:
                        idx_year2 = -1
                        for i in range(idx_year + 1, len(parts)):
                            if parts[i].isdigit() and len(parts[i]) == 4 and int(parts[i]) >= 2020:
                                idx_year2 = i
                                break
                        if idx_year2 != -1:
                            end_str = " ".join(parts[idx_year2 - 3 : idx_year2 + 1])
                            end_dt = datetime.strptime(end_str, "%b %d %H:%M:%S %Y")
                        else:
                            end_dt = start_dt
                    sessions.append((start_dt, end_dt))
            except Exception:
                continue
    except Exception:
        pass

    total_sec_today = 0.0
    total_sec_month = 0.0

    for s, e in sessions:
        st = max(s, today_start)
        et = min(e, now)
        if et > st:
            total_sec_today += (et - st).total_seconds()

        sm = max(s, month_start)
        em = min(e, now)
        if em > sm:
            total_sec_month += (em - sm).total_seconds()

    if total_sec_today == 0.0:
        try:
            with open("/proc/uptime") as f:
                up = float(f.read().split()[0])
                total_sec_today = min(up, (now - today_start).total_seconds())
                total_sec_month = up
        except Exception:
            pass

    return total_sec_today, total_sec_month


def get_rpi_info() -> Dict[str, Any]:
    """
    Connects via SSH to Raspberry Pi Zero W to get uptime, model, and temperature.
    Uses a strict short timeout to avoid delays when offline.
    """
    info: Dict[str, Any] = {
        "online": False,
        "host": None,
        "model": "Raspberry Pi Zero W",
        "temp": None,
        "total_uptime_sec": 0.0,
        "sec_today": 0.0,
        "sec_month": 0.0,
    }
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    month_start = datetime(now.year, now.month, 1, 0, 0, 0)
    max_today_sec = (now - today_start).total_seconds()
    max_month_sec = (now - month_start).total_seconds()

    for host in RPI_SSH_HOSTS:
        try:
            cmd = (
                "cat /proc/uptime; echo \"__DELIM__\"; "
                "cat /proc/device-tree/model 2>/dev/null; echo \"__DELIM__\"; "
                "vcgencmd measure_temp 2>/dev/null || cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null"
            )
            res = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", host, cmd],
                capture_output=True, text=True, timeout=3
            )
            if res.returncode == 0:
                parts = res.stdout.split("__DELIM__")
                uptime_part = parts[0].strip() if len(parts) > 0 else ""
                model_part = parts[1].strip().replace("\x00", "") if len(parts) > 1 else ""
                temp_part = parts[2].strip() if len(parts) > 2 else ""

                if uptime_part:
                    total_up = float(uptime_part.split()[0])
                    info["online"] = True
                    info["host"] = host.replace("rpi@", "")
                    info["total_uptime_sec"] = total_up
                    info["sec_today"] = min(total_up, max_today_sec)
                    info["sec_month"] = min(total_up, max_month_sec)

                if model_part:
                    info["model"] = model_part

                if temp_part:
                    if "temp=" in temp_part:
                        info["temp"] = temp_part.replace("temp=", "").replace("'", "°")
                    elif temp_part.isdigit():
                        info["temp"] = f"{int(temp_part)/1000.0:.1f}°C"
                break
        except Exception:
            continue

    return info


def format_duration(seconds: float) -> str:
    """Formats seconds into 'Xd Yh Zm' or 'Yh Zm'."""
    d = int(seconds // 86400)
    rem = seconds % 86400
    h = int(rem // 3600)
    m = int((rem % 3600) // 60)
    if d > 0:
        return f"{d}g {h}h {m}m"
    return f"{h}h {m}m"


def format_cost(cost: float) -> str:
    """Formats monetary cost in Euros."""
    if cost == 0:
        return "0.00 €"
    if cost < 0.01:
        return f"{cost:.3f} €"
    return f"{cost:.2f} €"


def get_power_report(
    kwh_cost: float = DEFAULT_KWH_COST,
    pc_avg_watts: float = DEFAULT_PC_AVG_WATTS,
    rpi_avg_watts: float = DEFAULT_RPI_AVG_WATTS
) -> str:
    """
    Generates a comprehensive power consumption and cost report
    for both the PC (ThinkPad L14) and Raspberry Pi Zero W.
    """
    # 1. PC Metrics
    sec_pc_today, sec_pc_month = get_pc_uptime_seconds()
    bat = get_battery_info()

    h_pc_today = sec_pc_today / 3600.0
    h_pc_month = sec_pc_month / 3600.0

    wh_pc_today = h_pc_today * pc_avg_watts
    wh_pc_month = h_pc_month * pc_avg_watts

    kwh_pc_today = wh_pc_today / 1000.0
    kwh_pc_month = wh_pc_month / 1000.0

    cost_pc_today = kwh_pc_today * kwh_cost
    cost_pc_month = kwh_pc_month * kwh_cost

    ac_str = "🔌 Rete elettrica (AC)" if bat["ac_online"] else "🔋 Batteria"
    bat_cap = f"{bat['capacity']}%" if bat["capacity"] is not None else "N/D"
    bat_status = bat.get("status", "N/D")

    rate_info = ""
    if bat["power_w"] is not None:
        rate_info = f" ({bat['power_w']:.1f} W)"

    # 2. Raspberry Pi Zero W Metrics
    rpi = get_rpi_info()
    rpi_online = rpi["online"]

    sec_rpi_today = rpi["sec_today"]
    sec_rpi_month = rpi["sec_month"]

    h_rpi_today = sec_rpi_today / 3600.0
    h_rpi_month = sec_rpi_month / 3600.0

    wh_rpi_today = h_rpi_today * rpi_avg_watts
    wh_rpi_month = h_rpi_month * rpi_avg_watts

    kwh_rpi_today = wh_rpi_today / 1000.0
    kwh_rpi_month = wh_rpi_month / 1000.0

    cost_rpi_today = kwh_rpi_today * kwh_cost
    cost_rpi_month = kwh_rpi_month * kwh_cost

    # 3. Totals
    tot_wh_today = wh_pc_today + (wh_rpi_today if rpi_online else 0.0)
    tot_kwh_today = tot_wh_today / 1000.0
    tot_cost_today = cost_pc_today + (cost_rpi_today if rpi_online else 0.0)

    tot_wh_month = wh_pc_month + (wh_rpi_month if rpi_online else 0.0)
    tot_kwh_month = tot_wh_month / 1000.0
    tot_cost_month = cost_pc_month + (cost_rpi_month if rpi_online else 0.0)

    # Build Report
    rpi_lines = []
    if rpi_online:
        temp_str = f" — Temp: `{rpi['temp']}`" if rpi["temp"] else ""
        rpi_lines.append(f"🍓 *{rpi['model']}*")
        rpi_lines.append(f"• Stato: 🟢 Online (`{rpi['host']}`){temp_str}")
        rpi_lines.append(f"• Uptime totale: `{format_duration(rpi['total_uptime_sec'])}` (Attivo 24/7)")
        rpi_lines.append(f"• Uptime periodo: Oggi `{format_duration(sec_rpi_today)}` | Questo Mese `{format_duration(sec_rpi_month)}`")
        rpi_lines.append(f"• Consumo stimato (@ ~{rpi_avg_watts:.1f}W): `{wh_rpi_today:.1f} Wh` ({kwh_rpi_today:.3f} kWh)")
        rpi_lines.append(f"• Spesa stimata: Oggi `{format_cost(cost_rpi_today)}` | Questo Mese `{format_cost(cost_rpi_month)}`")
    else:
        rpi_lines.append("🍓 *Raspberry Pi Zero W*")
        rpi_lines.append("• Stato: 🔴 Non raggiungibile / Spento")

    rpi_block = "\n".join(rpi_lines)

    report = (
        "⚡ *Report Consumi Energetici e Costi*\n\n"
        "💻 *PC: ThinkPad L14 Gen 2a* (Ryzen 5 PRO 5650U)\n"
        f"• Alimentazione: {ac_str} | Batteria: `{bat_cap}` ({bat_status}{rate_info})\n"
        f"• Uptime: Oggi `{format_duration(sec_pc_today)}` | Questo Mese `{format_duration(sec_pc_month)}`\n"
        f"• Consumo stimato (@ ~{pc_avg_watts:.0f}W): `{wh_pc_today:.1f} Wh` ({kwh_pc_today:.3f} kWh)\n"
        f"• Spesa stimata: Oggi `{format_cost(cost_pc_today)}` | Questo Mese `{format_cost(cost_pc_month)}`\n\n"
        f"{rpi_block}\n\n"
        "📊 *Totale Complessivo (PC + RPi0W):*\n"
        f"• *Oggi:* `{tot_wh_today:.1f} Wh` ({tot_kwh_today:.3f} kWh) ➔ *~{format_cost(tot_cost_today)}*\n"
        f"• *Questo Mese:* `{tot_wh_month:.1f} Wh` ({tot_kwh_month:.3f} kWh) ➔ *~{format_cost(tot_cost_month)}*\n\n"
        f"_(Tariffa energetica applicata: {kwh_cost:.2f} €/kWh)_"
    )
    return report


if __name__ == "__main__":
    print(get_power_report())
