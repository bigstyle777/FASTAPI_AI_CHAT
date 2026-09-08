"""Memory 领域：semantic / episodic 两类记忆的提取、检索与管理。

对外通路：
- 检索：retriever.retrieve_memories / build_memory_context —— 被 agent 的
  build_memory_messages（chat/services）与 MCP 工具 search_memory 调用；
- 提取→落库：extractor（LLM 结构化）→ manager.apply_actions（Celery 落库）；
- 生命周期：decay.reinforce_memories / run_decay、consolidator（由 task 触发）。
数据访问在 repository.py，排序在 retriever/reranker.py，SQL 不往外散。
"""
