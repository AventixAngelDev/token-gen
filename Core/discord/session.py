import datetime
import urllib.parse
from requests.structures import CaseInsensitiveDict

import curl_cffi
import wreq
import primp


class WreqCookieJar(dict):
    def get_dict(self):
        return dict(self)

class UnifiedResponse:
    def __init__(self, raw_resp, client_type):
        self._resp = raw_resp
        self._type = client_type

    @property
    def ok(self) -> bool:
        if self._type == "curl_cffi":
            return self._resp.ok
        elif self._type == "wreq":
            return self.status_code < 400
        elif self._type == "primp":
            return self._resp.status_code < 400
        return False

    @property
    def status_code(self) -> int:
        if self._type == "curl_cffi":
            return self._resp.status_code
        elif self._type == "wreq":
            return self._resp.status.as_int()
        elif self._type == "primp":
            return self._resp.status_code
        return 0

    @property
    def text(self) -> str:
        if self._type == "curl_cffi":
            return self._resp.text
        elif self._type == "wreq":
            return self._resp.text()
        elif self._type == "primp":
            return self._resp.text
        return ""

    @property
    def content(self) -> bytes:
        if self._type == "curl_cffi":
            return self._resp.content
        elif self._type == "wreq":
            return self._resp.bytes()
        elif self._type == "primp":
            return self._resp.content
        return b""

    def json(self, **kwargs):
        if self._type == "curl_cffi":
            return self._resp.json(**kwargs)
        elif self._type == "wreq":
            try:
                return self._resp.json(**kwargs)
            except Exception as e:
                from json import JSONDecodeError
                raise JSONDecodeError("Failed to decode JSON", "", 0) from e
        elif self._type == "primp":
            try:
                return self._resp.json()
            except Exception as e:
                from json import JSONDecodeError
                raise JSONDecodeError("Failed to decode JSON", "", 0) from e
        return {}

    @property
    def cookies(self):
        if self._type == "curl_cffi":
            return self._resp.cookies
        elif self._type == "wreq":
            return WreqCookieJar({c.name: c.value for c in self._resp.cookies})
        elif self._type == "primp":
            return WreqCookieJar(self._resp.cookies)
        return WreqCookieJar()

    @property
    def headers(self):
        if self._type == "curl_cffi":
            return self._resp.headers
        elif self._type == "wreq":
            hdrs = {}
            for k in self._resp.headers.keys():
                k_str = k.decode('utf-8', errors='ignore') if isinstance(k, bytes) else str(k)
                vals = self._resp.headers.get_all(k_str)
                vals_str = [v.decode('utf-8', errors='ignore') if isinstance(v, bytes) else str(v) for v in vals]
                hdrs[k_str] = ", ".join(vals_str)
            return CaseInsensitiveDict(hdrs)
        elif self._type == "primp":
            return CaseInsensitiveDict(self._resp.headers)
        return CaseInsensitiveDict()

class UnifiedSession:
    def __init__(self, client_type: str = "curl_cffi", impersonate: str = "chrome146", proxy: str | None = None):
        self.client_type = client_type.lower()
        self.impersonate = impersonate
        self.proxy = proxy
        self.headers = {}
        self.proxies = {}
        if proxy:
            self.proxies = {
                "http": f"http://{proxy}",
                "https": f"http://{proxy}",
            }
        self._cookies = WreqCookieJar()
        self._client = None
        self._init_client()

    def _init_client(self):
        if self.client_type == "curl_cffi":
            if curl_cffi is None:
                raise ImportError("curl_cffi is not installed")
            session = curl_cffi.requests.Session(impersonate=self.impersonate)
            if self.proxy:
                session.proxies = self.proxies.copy()
            self._client = session

        elif self.client_type == "wreq":
            if wreq is None:
                raise ImportError("wreq-python is not installed")
            
            emulation_val = wreq.Emulation.Chrome146
            if self.impersonate:
                num = "".join(filter(str.isdigit, self.impersonate))
                if num and hasattr(wreq.Emulation, f"Chrome{num}"):
                    emulation_val = getattr(wreq.Emulation, f"Chrome{num}")

            proxies_list = []
            if self.proxy:
                proxies_list.append(wreq.Proxy.all(f"http://{self.proxy}"))

            self._client = wreq.blocking.Client(
                emulation=emulation_val,
                proxies=proxies_list,
                cookie_store=True
            )

        elif self.client_type == "primp":
            if primp is None:
                raise ImportError("primp is not installed")

            impersonate_val = "chrome_120"
            if self.impersonate:
                num = "".join(filter(str.isdigit, self.impersonate))
                impersonate_val = f"chrome_{num}" if num else "chrome_120"

            proxy_str = f"http://{self.proxy}" if self.proxy else None

            try:
                self._client = primp.Client(
                    impersonate=impersonate_val,
                    proxy=proxy_str,
                    headers=self.headers
                )
            except ValueError:
                impersonate_val = "chrome_120"
                self._client = primp.Client(
                    impersonate=impersonate_val,
                    proxy=proxy_str,
                    headers=self.headers
                )

        else:
            raise ValueError(f"Unknown http_client type: {self.client_type}")

    @property
    def cookies(self):
        return self._cookies

    def _prepare_wreq_kwargs(self, kwargs):
        req_headers = kwargs.get("headers", {})
        merged = self.headers.copy()
        if req_headers:
            merged.update(req_headers)
        kwargs["headers"] = merged

        timeout = kwargs.get("timeout")
        if timeout is not None and isinstance(timeout, (int, float)):
            kwargs["timeout"] = datetime.timedelta(seconds=timeout)

        data = kwargs.pop("data", None)
        if data is not None:
            if isinstance(data, dict):
                kwargs["body"] = urllib.parse.urlencode(data)
                if not any(k.lower() == "content-type" for k in kwargs["headers"]):
                    kwargs["headers"]["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                kwargs["body"] = data

        return kwargs

    def _prepare_primp_kwargs(self, kwargs):
        req_headers = kwargs.get("headers", {})
        merged = self.headers.copy()
        if req_headers:
            merged.update(req_headers)
        kwargs["headers"] = merged

        data = kwargs.pop("data", None)
        if data is not None:
            if isinstance(data, dict):
                kwargs["data"] = data
            elif isinstance(data, str):
                kwargs["content"] = data.encode('utf-8')
            elif isinstance(data, bytes):
                kwargs["content"] = data

        return kwargs

    def request(self, method, url, **kwargs):
        if self.client_type == "curl_cffi":
            self._client.headers.update(self.headers)
            self._client.proxies = self.proxies.copy()
            raw_resp = self._client.request(method, url, **kwargs)
            resp = UnifiedResponse(raw_resp, self.client_type)
            self._cookies.update(resp.cookies)
            return resp

        elif self.client_type == "wreq":
            kwargs = self._prepare_wreq_kwargs(kwargs)
            method_val = getattr(wreq.Method, method.upper())
            raw_resp = self._client.request(method_val, url, **kwargs)
            resp = UnifiedResponse(raw_resp, self.client_type)
            self._cookies.update(resp.cookies)
            return resp

        elif self.client_type == "primp":
            kwargs = self._prepare_primp_kwargs(kwargs)
            raw_resp = self._client.request(method.upper(), url, **kwargs)
            resp = UnifiedResponse(raw_resp, self.client_type)
            self._cookies.update(resp.cookies)
            return resp

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)
