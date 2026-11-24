"""生成应用的静态检查（M9 第 1 步，提案 §5.4）。

生成的 HTML 来自模型，属于**不可信输入**（§8.4）。本模块在落盘前
逐项检查，任何一项不通过都不写入 staging。

**这些检查是纵深防御的一层，不是唯一防线。** 真正的保证来自
iframe 的 `sandbox` 与 CSP——静态检查是基于文本匹配的，可能被绕过；
浏览器的同源策略与内容安全策略不会。两层独立，任一失效另一层仍在。

**已实测的绕过手法**（本机 Edge 无头浏览器，2026-11-24）：

| 绕过写法 | 静态检查 | 浏览器 CSP |
|---|---|---|
| `window['fe'+'tch']('...')` | ❌ 放行 | ✅ 阻断（TypeError）|
| `window[String.fromCharCode(102,101,116,99,104)]('...')` | ❌ 放行 | ✅ 阻断 |
| `window['fetch']('...')` | ❌ 放行 | ✅ 阻断 |

三条绕过路径在浏览器里全部失败，网络面板零外部请求。

**另一处实测缺口（第 3 步发现）**：初版只扫描 `<script>` 正文，
`<li onclick="fetch('/x')">` 直接漏过——内联事件处理器同样是 JavaScript。
已改为把 `on*` 属性的值并入同一套检查（有测试）。这条缺口说明
"逐项列举代码出现的位置"很容易漏，因此检查函数刻意做得宽：宁可误杀。
这证明静态检查**不能**作为唯一防线——但也说明不必追求
"静态检查做到滴水不漏"，因为第二层确实可靠。

设计取向：**宁可误杀**。一个合法的应用被拒绝，用户能看懂原因并调整描述；
一个恶意的应用被放行，代价是安全边界被突破。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# 单文件应用的大小上限。超过它多半是把库内联进来了，
# 那样的产物既难审查，也不符合"简单应用"的定位。
MAX_APP_BYTES = 200 * 1024

# 版本号格式，与插件保持一致。
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True, slots=True)
class Issue:
    """一条检查发现的问题。`rule` 是稳定的机器码，便于测试与统计。"""

    rule: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    @property
    def messages(self) -> list[str]:
        return [issue.message for issue in self.issues]


# 禁止的 JavaScript 能力。用正则匹配源码文本。
#
# 注意这些是**兜底**而非主要防线：字符串拼接、编码等手法可能绕过。
# 真正的拦截来自 CSP 与 iframe sandbox。
_FORBIDDEN_JS = (
    (
        "network_fetch",
        re.compile(r"\bfetch\s*\(", re.IGNORECASE),
        "应用不能发起网络请求（fetch）",
    ),
    (
        "network_xhr",
        re.compile(r"\bXMLHttpRequest\b"),
        "应用不能发起网络请求（XMLHttpRequest）",
    ),
    (
        "network_websocket",
        re.compile(r"\bWebSocket\b"),
        "应用不能建立 WebSocket 连接",
    ),
    (
        "network_eventsource",
        re.compile(r"\bEventSource\b"),
        "应用不能建立 EventSource 连接",
    ),
    (
        "dynamic_import",
        re.compile(r"\bimport\s*\("),
        "应用不能使用动态 import",
    ),
    (
        "parent_access",
        re.compile(r"\b(?:window\.)?(?:parent|top)\s*[.\[]"),
        "应用不能访问父页面（parent/top）",
    ),
    (
        "cookie_access",
        re.compile(r"\bdocument\s*\.\s*cookie\b"),
        "应用不能访问 cookie",
    ),
    (
        "storage_access",
        re.compile(r"\b(?:localStorage|sessionStorage|indexedDB)\b"),
        "v1 应用不能存取数据（localStorage/sessionStorage/indexedDB）",
    ),
    (
        "code_eval",
        re.compile(r"\beval\s*\("),
        "应用不能使用 eval",
    ),
    (
        "function_constructor",
        re.compile(r"\bnew\s+Function\s*\("),
        "应用不能使用 new Function",
    ),
)

# 禁止的 HTML 标签：可加载外部资源或嵌套浏览上下文。
_FORBIDDEN_TAGS = frozenset({"script", "iframe", "object", "embed", "base", "form"})

# script 标签里允许出现的属性。src 会加载外部脚本，必须禁止。
_FORBIDDEN_SCRIPT_ATTRS = frozenset({"src", "nonce", "integrity"})

# 会引起网络请求的属性。
_FORBIDDEN_ATTRS = frozenset({"srcset", "ping", "action", "formaction"})

# 允许的外部样式/脚本协议：只有内联与 data: 是安全的。
_ALLOWED_URL_SCHEMES = ("data:", "#")


class _AppParser(HTMLParser):
    """收集文档结构信息。只解析，不执行、不渲染。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: list[str] = []
        self.handlers: list[str] = []
        self._in_script = False
        self._script_depth = 0
        self.has_html_element = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "html":
            self.has_html_element = True
        if tag == "script":
            self._in_script = True
            self._script_depth = 1
        for name, value in attrs:
            # 内联事件处理器（onclick=…）也是 JavaScript 代码。
            # 实测教训：初版只扫 <script> 正文，"onclick=\"fetch('/x')\"" 直接漏过。
            # CSP 的 unsafe-inline 同样允许事件处理器，因此这里必须一并检查。
            if value and name.lower().startswith("on"):
                self.handlers.append(value)
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False
            self._script_depth = 0

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.scripts.append(data)


def _check_structure(parser: _AppParser) -> list[Issue]:
    issues: list[Issue] = []

    for tag, attrs in parser.tags:
        if tag in _FORBIDDEN_TAGS - {"script"}:
            issues.append(Issue("forbidden_tag", f"不允许使用 <{tag}> 标签"))
            continue

        if tag == "script":
            forbidden = _FORBIDDEN_SCRIPT_ATTRS & set(attrs)
            if forbidden:
                names = "、".join(sorted(forbidden))
                issues.append(
                    Issue(
                        "external_script",
                        f"<script> 不能带 {names} 属性：应用必须是单文件自包含的",
                    )
                )

        for attr in _FORBIDDEN_ATTRS & set(attrs):
            issues.append(Issue("forbidden_attribute", f"不允许使用 {attr} 属性"))

        # 链接类属性只能是相对锚点或 data:（禁用外链样式/资源）。
        for attr in ("href", "src"):
            value = attrs.get(attr)
            if value is None:
                continue
            lowered = value.strip().lower()
            if lowered.startswith(_ALLOWED_URL_SCHEMES) or lowered == "":
                continue
            issues.append(
                Issue(
                    "external_resource",
                    f"不允许加载外部资源（{attr}={value[:60]}），应用必须自包含",
                )
            )

    return issues


def _check_scripts(scripts: list[str], handlers: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    # 内联事件处理器与 <script> 正文同等对待。
    joined = "\n".join([*scripts, *handlers])

    for rule, pattern, message in _FORBIDDEN_JS:
        match = pattern.search(joined)
        if match is not None:
            snippet = match.group(0).strip()
            issues.append(Issue(rule, f"{message}（发现 {snippet!r}）"))

    return issues


def validate_app(html: str, *, max_bytes: int = MAX_APP_BYTES) -> ValidationResult:
    """检查一份生成的应用。返回全部问题，而不是遇到第一个就停。

    一次列出所有问题，用户一次就能知道要改哪里，而不是"修一个报一个"。
    """
    issues: list[Issue] = []

    encoded = html.encode("utf-8")
    if not html.strip():
        issues.append(Issue("empty", "应用内容是空的"))
        return ValidationResult(ok=False, issues=tuple(issues))

    if len(encoded) > max_bytes:
        issues.append(
            Issue(
                "too_large",
                f"应用大小 {len(encoded)} 字节，超过上限 {max_bytes}",
            )
        )

    # 刻意**不去掉注释**：注释里出现禁用调用同样拒绝。
    # 理由：分辨"真注释"与"字符串拼接出来的伪注释"需要完整的 JS 解析，
    # 而基于文本的检查做不到。宁可误杀，让用户看懂原因后调整描述。
    parser = _AppParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - HTMLParser 极少抛错，兜底为"无法解析"
        issues.append(Issue("unparsable", "应用 HTML 无法解析"))
        return ValidationResult(ok=False, issues=tuple(issues))

    if not parser.has_html_element and not parser.tags:
        issues.append(Issue("no_content", "应用没有任何 HTML 内容"))

    issues.extend(_check_structure(parser))
    issues.extend(_check_scripts(parser.scripts, parser.handlers))

    return ValidationResult(ok=not issues, issues=tuple(issues))


def sanitize_app_id(app_id: str) -> str:
    """校验应用 id。

    id 会直接成为目录名与 URL 片段，因此限定为小写字母、数字、连字符，
    且必须以字母开头。这样就不可能出现路径分隔符、`..` 或空值。
    """
    if not re.match(r"^[a-z][a-z0-9-]{1,62}$", app_id):
        raise ValueError("应用 id 只能用小写字母、数字与连字符，以字母开头，长度 2–63")
    return app_id
