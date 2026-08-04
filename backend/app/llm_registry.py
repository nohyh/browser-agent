"""按调用方复用客户端，并按模型解析本轮 Agent LLM。"""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.llm import AgentLLM


@dataclass
class LLMEndpointRecord:
    """一个 API 地址及用户允许在对话中选择的模型。"""

    endpoint_id: str
    name: str
    api_url: str
    api_key: str
    models: tuple[str, ...]
    client: Any
    llms: dict[str, AgentLLM] = field(default_factory=dict)

    def resolve(self, model: str) -> AgentLLM:
        if model not in self.models:
            raise KeyError(
                f"Model '{model}' is not enabled for endpoint "
                f"'{self.endpoint_id}'"
            )
        llm = self.llms.get(model)
        if llm is None:
            llm = AgentLLM(
                self.client,
                model=model,
                endpoint_id=self.endpoint_id,
            )
            self.llms[model] = llm
        return llm


class LLMRegistry:
    """保存本地配置；更新时保留旧客户端，避免中断正在执行的任务。"""

    def __init__(self):
        self._endpoints: dict[str, LLMEndpointRecord] = {}
        self._retired_clients: list[Any] = []

    def replace(
        self,
        endpoints: Iterable[Any],
        client_factory: Callable[..., Any],
    ) -> None:
        previous = self._endpoints
        configured: dict[str, LLMEndpointRecord] = {}

        for endpoint in endpoints:
            endpoint_id = endpoint.id
            api_key = endpoint.api_key.get_secret_value()
            existing = previous.get(endpoint_id)
            if (
                existing is not None
                and existing.api_url == endpoint.api_url
                and existing.api_key == api_key
            ):
                existing.name = endpoint.name
                existing.models = tuple(endpoint.models)
                existing.llms = {
                    model: llm
                    for model, llm in existing.llms.items()
                    if model in existing.models
                }
                configured[endpoint_id] = existing
                continue

            client = client_factory(
                api_key=api_key,
                base_url=endpoint.api_url,
            )
            configured[endpoint_id] = LLMEndpointRecord(
                endpoint_id=endpoint_id,
                name=endpoint.name,
                api_url=endpoint.api_url,
                api_key=api_key,
                models=tuple(endpoint.models),
                client=client,
            )

        reused_client_ids = {
            id(endpoint.client) for endpoint in configured.values()
        }
        for endpoint in previous.values():
            if id(endpoint.client) not in reused_client_ids:
                self._retired_clients.append(endpoint.client)
        self._endpoints = configured

    def resolve(self, endpoint_id: str, model: str) -> AgentLLM:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None:
            raise KeyError(f"LLM endpoint '{endpoint_id}' is not configured")
        return endpoint.resolve(model)

    def first(self) -> AgentLLM | None:
        for endpoint in self._endpoints.values():
            if endpoint.models:
                return endpoint.resolve(endpoint.models[0])
        return None

    async def close(self) -> None:
        clients = [
            *(endpoint.client for endpoint in self._endpoints.values()),
            *self._retired_clients,
        ]
        seen: set[int] = set()
        for client in clients:
            if id(client) in seen:
                continue
            seen.add(id(client))
            await client.close()
        self._endpoints.clear()
        self._retired_clients.clear()
