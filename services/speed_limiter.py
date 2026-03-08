import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)

# We use tc (traffic control) with HTB (Hierarchical Token Bucket)
# to limit bandwidth per client IP.
#
# Structure:
#   root qdisc (htb) on main interface
#   └── class 1:1 (total bandwidth)
#       └── class 1:N (per-IP limit)
#           └── filter matching dst IP -> class 1:N
#
# For download limiting (server -> client), we apply on the outgoing interface.
# Class IDs are derived from IP address to avoid collisions.

INTERFACE = None  # Will be auto-detected


def _get_interface() -> str:
    global INTERFACE
    if INTERFACE:
        return INTERFACE
    try:
        result = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True, text=True, timeout=5
        )
        for part in result.stdout.split():
            if part == "dev":
                idx = result.stdout.split().index("dev")
                INTERFACE = result.stdout.split()[idx + 1]
                break
        if not INTERFACE:
            INTERFACE = "eth0"
    except Exception:
        INTERFACE = "eth0"
    logger.info(f"Using network interface: {INTERFACE}")
    return INTERFACE


def _ip_to_class_id(ip: str) -> int:
    """Convert IP to a unique class ID (2-9999)."""
    parts = ip.split(".")
    if len(parts) == 4:
        return (int(parts[2]) * 256 + int(parts[3])) % 9998 + 2
    # IPv6 - use hash
    return hash(ip) % 9998 + 2


def _run(cmd: str) -> bool:
    try:
        subprocess.run(cmd, shell=True, capture_output=True, timeout=10, check=True)
        return True
    except subprocess.CalledProcessError:
        return False
    except Exception as e:
        logger.error(f"tc command failed: {cmd} -> {e}")
        return False


async def init_tc():
    """Initialize the root qdisc. Call once at startup."""
    iface = _get_interface()

    # Remove existing qdisc (ignore errors)
    _run(f"tc qdisc del dev {iface} root 2>/dev/null")

    # Create root HTB qdisc
    _run(f"tc qdisc add dev {iface} root handle 1: htb default 9999")

    # Default class (unlimited)
    _run(f"tc class add dev {iface} parent 1: classid 1:9999 htb rate 1000mbit")

    logger.info("TC initialized")


async def set_speed_limit(ip: str, speed_mbps: float):
    """Set speed limit for a specific IP address."""
    if speed_mbps <= 0:
        await remove_speed_limit(ip)
        return

    iface = _get_interface()
    class_id = _ip_to_class_id(ip)
    rate = f"{speed_mbps}mbit"
    burst = f"{max(int(speed_mbps * 1.5), 15)}k"

    # Remove existing class and filter for this IP (ignore errors)
    _run(f"tc filter del dev {iface} parent 1: protocol ip prio 1 u32 match ip dst {ip}/32 2>/dev/null")
    _run(f"tc class del dev {iface} parent 1: classid 1:{class_id} 2>/dev/null")

    # Add class with speed limit
    _run(f"tc class add dev {iface} parent 1:1 classid 1:{class_id} htb rate {rate} burst {burst}")

    # Add filter to match destination IP
    _run(f"tc filter add dev {iface} parent 1: protocol ip prio 1 u32 match ip dst {ip}/32 flowid 1:{class_id}")

    logger.debug(f"Speed limit set: {ip} -> {speed_mbps} Mbps (class 1:{class_id})")


async def remove_speed_limit(ip: str):
    """Remove speed limit for a specific IP address."""
    iface = _get_interface()
    class_id = _ip_to_class_id(ip)

    _run(f"tc filter del dev {iface} parent 1: protocol ip prio 1 u32 match ip dst {ip}/32 2>/dev/null")
    _run(f"tc class del dev {iface} parent 1: classid 1:{class_id} 2>/dev/null")

    logger.debug(f"Speed limit removed: {ip}")


async def apply_speed_limit_for_client(api, email: str, speed_mbps: float):
    """Apply speed limit to all known IPs of a client."""
    ips = await api.get_client_ips(email)
    for ip in ips:
        if speed_mbps > 0:
            await set_speed_limit(ip, speed_mbps)
        else:
            await remove_speed_limit(ip)
    return ips


async def clear_all_limits():
    """Remove all tc rules."""
    iface = _get_interface()
    _run(f"tc qdisc del dev {iface} root 2>/dev/null")
    logger.debug("All TC rules cleared")
