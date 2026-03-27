import aiohttp
import json
import ssl
import logging
import uuid as uuid_lib

logger = logging.getLogger(__name__)


class XUIApi:
    def __init__(self, config: dict):
        self.base_url = config["panel"]["url"].rstrip("/")
        self.base_path = config["panel"]["base_path"].rstrip("/")
        self.username = config["panel"]["username"]
        self.password = config["panel"]["password"]
        self.inbound_id = config["panel"].get("inbound_id")
        self.domain = config.get("domain") or None
        self.cookie = None
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _api_url(self, path: str) -> str:
        return f"{self.base_url}{self.base_path}/panel/api/inbounds{path}"

    def _login_url(self) -> str:
        return f"{self.base_url}{self.base_path}/login"

    async def login(self) -> bool:
        session = await self._get_session()
        try:
            async with session.post(self._login_url(), json={
                "username": self.username,
                "password": self.password
            }) as resp:
                data = await resp.json()
                if data.get("success"):
                    cookies = resp.cookies
                    for key, cookie in cookies.items():
                        if "3x-ui" in key.lower() or "session" in key.lower():
                            self.cookie = f"{key}={cookie.value}"
                            break
                    if not self.cookie and cookies:
                        first = list(cookies.items())[0]
                        self.cookie = f"{first[0]}={first[1].value}"
                    # Also try Set-Cookie header
                    if not self.cookie:
                        sc = resp.headers.get("Set-Cookie", "")
                        if sc:
                            self.cookie = sc.split(";")[0]
                    logger.info("Login successful")
                    return True
                logger.error(f"Login failed: {data}")
                return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def _request(self, method: str, path: str, **kwargs) -> dict | None:
        if not self.cookie:
            if not await self.login():
                return None

        session = await self._get_session()
        headers = {"Cookie": self.cookie}

        try:
            async with session.request(method, self._api_url(path), headers=headers, **kwargs) as resp:
                if resp.status == 401 or resp.status == 307:
                    # Session expired, re-login
                    self.cookie = None
                    if await self.login():
                        headers = {"Cookie": self.cookie}
                        async with session.request(method, self._api_url(path), headers=headers, **kwargs) as resp2:
                            return await resp2.json()
                    return None
                return await resp.json()
        except Exception as e:
            logger.error(f"API request error {path}: {e}")
            return None

    # --- Inbounds ---

    async def list_inbounds(self) -> list | None:
        data = await self._request("GET", "/list")
        if data and data.get("success"):
            return data.get("obj", [])
        return None

    async def get_inbound(self, inbound_id: int = None) -> dict | None:
        iid = inbound_id or self.inbound_id
        data = await self._request("GET", f"/get/{iid}")
        if data and data.get("success"):
            return data.get("obj")
        return None

    # --- Clients ---

    async def get_clients(self, inbound_id: int = None) -> list:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            return []
        settings = json.loads(inbound.get("settings", "{}"))
        return settings.get("clients", [])

    async def add_client(self, email: str, device_limit: int = 2,
                         total_gb: int = 0, flow: str = "xtls-rprx-vision",
                         inbound_id: int = None) -> dict | None:
        iid = inbound_id or self.inbound_id
        if not iid:
            logger.error("No inbound_id specified for add_client")
            return None

        client_uuid = str(uuid_lib.uuid4())
        sub_id = uuid_lib.uuid4().hex[:16]

        client = {
            "id": client_uuid,
            "flow": flow,
            "email": email,
            "limitIp": device_limit,
            "totalGB": total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0,
            "expiryTime": 0,
            "enable": True,
            "tgId": "",
            "subId": sub_id,
            "reset": 0
        }

        payload = {
            "id": iid,
            "settings": json.dumps({"clients": [client]})
        }

        data = await self._request("POST", "/addClient", json=payload)
        if data and data.get("success"):
            logger.info(f"Client {email} added with UUID {client_uuid}")
            return client
        logger.error(f"Failed to add client {email}: {data}")
        return None

    async def update_client(self, client_uuid: str, updates: dict, inbound_id: int = None) -> bool:
        iid = inbound_id or self.inbound_id
        inbound = await self.get_inbound(iid)
        if not inbound:
            return False

        settings = json.loads(inbound.get("settings", "{}"))
        clients = settings.get("clients", [])

        client = None
        for c in clients:
            if c["id"] == client_uuid:
                client = c
                break

        if not client:
            logger.error(f"Client UUID {client_uuid} not found")
            return False

        client.update(updates)

        payload = {
            "id": inbound.get("id", iid),
            "settings": json.dumps({"clients": [client]})
        }

        data = await self._request("POST", f"/updateClient/{client_uuid}", json=payload)
        if data and data.get("success"):
            return True
        logger.error(f"Failed to update client {client_uuid}: {data}")
        return False

    async def delete_client(self, client_uuid: str, inbound_id: int = None) -> bool:
        iid = inbound_id or self.inbound_id
        data = await self._request("POST", f"/{iid}/delClient/{client_uuid}")
        if data and data.get("success"):
            return True
        logger.error(f"Failed to delete client {client_uuid}: {data}")
        return False

    async def enable_client(self, client_uuid: str, enable: bool, inbound_id: int = None) -> bool:
        return await self.update_client(client_uuid, {"enable": enable}, inbound_id=inbound_id)

    # --- Traffic ---

    async def get_client_traffic(self, email: str) -> dict | None:
        data = await self._request("GET", f"/getClientTraffics/{email}")
        if data and data.get("success") and data.get("obj"):
            obj = data["obj"]
            if isinstance(obj, list):
                # Find the one matching our inbound
                for item in obj:
                    if item.get("email") == email:
                        return item
                return obj[0] if obj else None
            return obj
        return None

    async def get_all_client_traffics(self) -> list:
        """Get traffic for all clients by iterating."""
        clients = await self.get_clients()
        traffics = []
        for client in clients:
            traffic = await self.get_client_traffic(client["email"])
            if traffic:
                traffics.append(traffic)
        return traffics

    async def reset_client_traffic(self, email: str, inbound_id: int = None) -> bool:
        iid = inbound_id or self.inbound_id
        data = await self._request("POST", f"/{iid}/resetClientTraffic/{email}")
        if data and data.get("success"):
            return True
        return False

    async def reset_all_traffics(self) -> bool:
        data = await self._request("POST", "/resetAllTraffics")
        if data and data.get("success"):
            return True
        logger.error(f"reset_all_traffics failed: {data}")
        return False

    # --- Client IPs ---

    async def get_client_ips(self, email: str) -> list:
        data = await self._request("POST", f"/clientIps/{email}")
        if data and data.get("success"):
            obj = data.get("obj", "")
            if isinstance(obj, str):
                if obj and obj != "No IP Record":
                    return [ip.strip().split(" ")[0] for ip in obj.split(",") if ip.strip()]
                return []
            if isinstance(obj, list):
                # Items can be "IP (date)" format — extract just the IP
                return [item.split(" ")[0] for item in obj if item]
        return []

    async def get_client_ips_with_dates(self, email: str) -> dict:
        """Returns {ip: datetime_str} from clientIps API."""
        data = await self._request("POST", f"/clientIps/{email}")
        if data and data.get("success"):
            obj = data.get("obj", "")
            items = []
            if isinstance(obj, str) and obj and obj != "No IP Record":
                items = [s.strip() for s in obj.split(",") if s.strip()]
            elif isinstance(obj, list):
                items = [s for s in obj if s]
            result = {}
            for item in items:
                parts = item.split(" (", 1)
                ip = parts[0].strip()
                date = parts[1].rstrip(")") if len(parts) > 1 else ""
                result[ip] = date
            return result
        return {}

    async def clear_client_ips(self, email: str) -> bool:
        data = await self._request("POST", f"/clearClientIps/{email}")
        return data and data.get("success", False)

    # --- Online Clients ---

    async def get_online_clients(self) -> list:
        data = await self._request("POST", "/onlines")
        if data and data.get("success"):
            return data.get("obj", []) or []
        return []

    # --- VLESS Key Generation ---

    def _get_connection_address(self, inbound: dict) -> str:
        """Determine connection address for VLESS URI from inbound settings."""
        if self.domain:
            return self.domain

        listen = inbound.get("listen", "")
        if listen and listen not in ("0.0.0.0", "::"):
            return listen

        stream = json.loads(inbound.get("streamSettings", "{}"))
        reality = stream.get("realitySettings", {})

        server_names = reality.get("serverNames", [])
        if server_names:
            return server_names[0]

        dest = reality.get("dest", "")
        if dest:
            return dest.split(":")[0] if ":" in dest else dest

        logger.warning("Could not determine connection address from inbound settings")
        return "localhost"

    async def generate_vless_key(self, client_uuid: str, email: str, inbound_id: int = None) -> str | None:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            return None

        stream = json.loads(inbound.get("streamSettings", "{}"))
        reality = stream.get("realitySettings", {})

        pbk = reality.get("settings", {}).get("publicKey", "")
        if not pbk:
            pbk = reality.get("publicKey", "")

        short_ids = reality.get("shortIds", [])
        sid = short_ids[0] if short_ids else ""

        server_names = reality.get("serverNames", [])
        sni = server_names[0] if server_names else ""

        port = inbound.get("port", 443)
        address = self._get_connection_address(inbound)

        key = (
            f"vless://{client_uuid}@{address}:{port}"
            f"?type=tcp&security=reality"
            f"&pbk={pbk}&fp=chrome"
            f"&sni={sni}&sid={sid}"
            f"&spx=%2F&flow=xtls-rprx-vision"
            f"#{email}"
        )
        return key

    # --- All inbounds ---

    async def get_all_clients(self) -> list[tuple[int, str, int, list]]:
        """Returns [(inbound_id, remark, port, [clients]), ...] sorted by inbound id."""
        inbounds = await self.list_inbounds()
        if not inbounds:
            return []
        result = []
        for ib in sorted(inbounds, key=lambda x: x["id"]):
            settings = json.loads(ib.get("settings", "{}"))
            clients = settings.get("clients", [])
            result.append((ib["id"], ib.get("remark", f"Inbound {ib['id']}"), ib.get("port", 0), clients))
        return result

    # --- Sync existing clients ---

    async def sync_existing_clients(self) -> list:
        """Get all existing clients from all inbounds."""
        all_clients = await self.get_all_clients()
        result = []
        for iid, remark, port, clients in all_clients:
            for c in clients:
                c["_inbound_id"] = iid
                result.append(c)
        return result
