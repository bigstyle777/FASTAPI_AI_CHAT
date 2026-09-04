"""Memory 领域：semantic / episodic 两类记忆的提取、检索与管理。

对外的三个入口都在 manager.py 的 MemoryManager 上：
extract（LLM 提取）→ apply（落库）→ retrieve（混合检索）。
数据访问在 repository.py，排序在 retriever.py，SQL 不往外散。
"""
