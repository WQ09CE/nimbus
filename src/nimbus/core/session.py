"""
Nimbus v2 Session Management - Persistent Conversation State

Session 管理器负责：
1. 会话持久化（JSONL 格式，类似 Pi）
2. Tree 结构支持（分支和回溯）
3. 会话恢复和导航

设计参考 Pi 的 SessionManager，但简化以适应 Nimbus 的架构。

Storage Format (JSONL):
    {"id": "abc123", "parentId": null, "type": "user", "data": {...}, "timestamp": 1234567890.0}
    {"id": "def456", "parentId": "abc123", "type": "assistant", "data": {...}, "timestamp": 1234567891.0}
    ...

Tree Structure:
    每个 entry 有 id 和 parentId，形成树状结构。
    支持从任意节点分支，所有历史保存在同一个文件中。
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Iterator

from nimbus.core.memory.context import Message


# =============================================================================
# Entry Types
# =============================================================================

EntryType = Literal[
    "user",           # 用户消息
    "assistant",      # 助手消息
    "tool_result",    # 工具结果
    "compaction",     # 压缩摘要
    "branch_summary", # 分支摘要
    "frame_push",     # Context Stack push
    "frame_pop",      # Context Stack pop
    "model_change",   # 模型切换
    "custom",         # 自定义消息
]


@dataclass
class SessionEntry:
    """
    会话条目 - JSONL 中的单行记录。
    
    设计原则：
    - 每个条目都有唯一 id
    - parentId 指向逻辑上的前一个条目（形成树结构）
    - type 标识条目类型
    - data 存储实际内容
    """
    id: str
    parent_id: Optional[str]
    type: EntryType
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    
    # 可选的元数据
    frame_id: Optional[str] = None  # 所属的 Stack Frame
    label: Optional[str] = None     # 用户标签（书签）
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        d = {
            "id": self.id,
            "parentId": self.parent_id,
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        if self.frame_id:
            d["frameId"] = self.frame_id
        if self.label:
            d["label"] = self.label
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionEntry":
        """从字典创建"""
        return cls(
            id=d["id"],
            parent_id=d.get("parentId"),
            type=d["type"],
            data=d["data"],
            timestamp=d.get("timestamp", time.time()),
            frame_id=d.get("frameId"),
            label=d.get("label"),
        )
    
    def to_message(self) -> Optional[Message]:
        """转换回 Message（用于恢复会话）"""
        if self.type == "user":
            return Message(
                role="user",
                content=self.data.get("content", ""),
                meta={"entry_id": self.id}
            )
        elif self.type == "assistant":
            return Message(
                role="assistant",
                content=self.data.get("content"),
                tool_calls=self.data.get("tool_calls"),
                meta={"entry_id": self.id}
            )
        elif self.type == "tool_result":
            return Message(
                role="tool",
                content=self.data.get("content", ""),
                name=self.data.get("name"),
                tool_call_id=self.data.get("tool_call_id"),
                meta={"entry_id": self.id}
            )
        elif self.type == "compaction":
            # Compaction 作为 system 消息
            return Message(
                role="system",
                content=f"[Previous conversation summary]\n{self.data.get('summary', '')}",
                meta={"entry_id": self.id, "is_compaction": True}
            )
        return None


# =============================================================================
# Session Manager
# =============================================================================

class SessionManager:
    """
    会话管理器 - 持久化和恢复会话状态。
    
    核心功能：
    1. 追加条目到会话文件（JSONL）
    2. 维护 Tree 结构（通过 parentId）
    3. 获取当前分支的所有条目
    4. 支持导航到任意历史节点
    
    Example:
        sm = SessionManager(session_dir=Path("~/.nimbus/sessions"))
        sm.new_session()
        
        # 追加消息
        sm.append_message(user_message)
        sm.append_message(assistant_message)
        
        # 获取当前分支
        branch = sm.get_branch()
        
        # 导航到历史节点
        sm.navigate_to(entry_id)
    """
    
    def __init__(
        self,
        session_dir: Optional[Path] = None,
        session_file: Optional[Path] = None,
    ):
        """
        初始化会话管理器。
        
        Args:
            session_dir: 会话存储目录
            session_file: 直接指定会话文件（覆盖 session_dir）
        """
        self._session_dir = session_dir or Path.home() / ".nimbus" / "sessions"
        self._session_file = session_file
        
        # 内存中的条目缓存
        self._entries: List[SessionEntry] = []
        self._entries_by_id: Dict[str, SessionEntry] = {}
        
        # 当前叶子节点（最新条目）
        self._leaf_id: Optional[str] = None
        
        # 会话 ID
        self._session_id: str = ""
        
        # 确保目录存在
        if not self._session_file:
            self._session_dir.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # Session Lifecycle
    # =========================================================================
    
    def new_session(self, parent_session: Optional[str] = None) -> str:
        """
        创建新会话。
        
        Args:
            parent_session: 可选的父会话路径（用于追踪来源）
        
        Returns:
            新会话的 ID
        """
        self._session_id = uuid.uuid4().hex[:12]
        self._entries = []
        self._entries_by_id = {}
        self._leaf_id = None
        
        if not self._session_file:
            # 按日期组织会话文件
            date_str = time.strftime("%Y-%m-%d")
            session_subdir = self._session_dir / date_str
            session_subdir.mkdir(parents=True, exist_ok=True)
            self._session_file = session_subdir / f"{self._session_id}.jsonl"
        
        # 写入会话头（可选的元数据）
        if parent_session:
            self._write_meta({"parent_session": parent_session})
        
        return self._session_id
    
    def load_session(self, session_file: Path) -> bool:
        """
        加载已有会话。
        
        Args:
            session_file: 会话文件路径
        
        Returns:
            是否成功加载
        """
        if not session_file.exists():
            return False
        
        self._session_file = session_file
        self._session_id = session_file.stem
        self._entries = []
        self._entries_by_id = {}
        self._leaf_id = None
        
        # 读取所有条目
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # 跳过元数据行
                    if data.get("_meta"):
                        continue
                    entry = SessionEntry.from_dict(data)
                    self._entries.append(entry)
                    self._entries_by_id[entry.id] = entry
                    # 更新叶子节点（最后一个条目）
                    self._leaf_id = entry.id
                except json.JSONDecodeError:
                    continue
        
        return True
    
    def get_session_file(self) -> Optional[Path]:
        """获取当前会话文件路径"""
        return self._session_file
    
    def get_session_id(self) -> str:
        """获取当前会话 ID"""
        return self._session_id
    
    # =========================================================================
    # Entry Management
    # =========================================================================
    
    def append_entry(self, entry: SessionEntry) -> str:
        """
        追加条目到会话。
        
        Args:
            entry: 要追加的条目
        
        Returns:
            条目 ID
        """
        # 设置 parent_id 为当前叶子
        if entry.parent_id is None:
            entry.parent_id = self._leaf_id
        
        # 添加到内存
        self._entries.append(entry)
        self._entries_by_id[entry.id] = entry
        self._leaf_id = entry.id
        
        # 持久化
        self._persist_entry(entry)
        
        return entry.id
    
    def append_message(self, message: Message, frame_id: Optional[str] = None) -> str:
        """
        追加消息到会话（便捷方法）。
        
        Args:
            message: Message 对象
            frame_id: 所属的 Stack Frame ID
        
        Returns:
            条目 ID
        """
        entry_type: EntryType
        data: Dict[str, Any] = {}
        
        if message.role == "user":
            entry_type = "user"
            data["content"] = message.content
        elif message.role == "assistant":
            entry_type = "assistant"
            data["content"] = message.content
            if message.tool_calls:
                data["tool_calls"] = message.tool_calls
        elif message.role == "tool":
            entry_type = "tool_result"
            data["content"] = message.content
            data["name"] = message.name
            data["tool_call_id"] = message.tool_call_id
        else:
            entry_type = "custom"
            data["content"] = message.content
            data["role"] = message.role
        
        entry = SessionEntry(
            id=uuid.uuid4().hex[:12],
            parent_id=self._leaf_id,
            type=entry_type,
            data=data,
            frame_id=frame_id,
        )
        
        return self.append_entry(entry)
    
    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        追加压缩摘要。
        
        Args:
            summary: 压缩后的摘要
            first_kept_entry_id: 保留的第一个条目 ID
            tokens_before: 压缩前的 token 数
            details: 额外细节
        
        Returns:
            条目 ID
        """
        entry = SessionEntry(
            id=uuid.uuid4().hex[:12],
            parent_id=self._leaf_id,
            type="compaction",
            data={
                "summary": summary,
                "firstKeptEntryId": first_kept_entry_id,
                "tokensBefore": tokens_before,
                "details": details or {},
            },
        )
        return self.append_entry(entry)
    
    def append_frame_event(
        self,
        event_type: Literal["frame_push", "frame_pop"],
        frame_id: str,
        goal: str,
        result: Optional[str] = None,
    ) -> str:
        """
        追加 Context Stack 事件。
        
        Args:
            event_type: "frame_push" 或 "frame_pop"
            frame_id: Frame ID
            goal: Frame 目标
            result: pop 时的结果
        
        Returns:
            条目 ID
        """
        data = {"frameId": frame_id, "goal": goal}
        if result is not None:
            data["result"] = result
        
        entry = SessionEntry(
            id=uuid.uuid4().hex[:12],
            parent_id=self._leaf_id,
            type=event_type,
            data=data,
            frame_id=frame_id,
        )
        return self.append_entry(entry)
    
    # =========================================================================
    # Branch Navigation
    # =========================================================================
    
    def get_entries(self) -> List[SessionEntry]:
        """获取所有条目"""
        return self._entries.copy()
    
    def get_branch(self, leaf_id: Optional[str] = None) -> List[SessionEntry]:
        """
        获取从根到指定叶子的分支。
        
        Args:
            leaf_id: 叶子节点 ID，默认为当前叶子
        
        Returns:
            分支上的所有条目（从根到叶子顺序）
        """
        target_id = leaf_id or self._leaf_id
        if not target_id:
            return []
        
        # 从叶子向上回溯
        branch = []
        current_id = target_id
        while current_id:
            entry = self._entries_by_id.get(current_id)
            if not entry:
                break
            branch.append(entry)
            current_id = entry.parent_id
        
        # 反转得到从根到叶子的顺序
        branch.reverse()
        return branch
    
    def get_leaf_id(self) -> Optional[str]:
        """获取当前叶子节点 ID"""
        return self._leaf_id
    
    def navigate_to(self, entry_id: str) -> bool:
        """
        导航到指定条目（切换分支）。
        
        Args:
            entry_id: 目标条目 ID
        
        Returns:
            是否成功导航
        """
        if entry_id not in self._entries_by_id:
            return False
        
        self._leaf_id = entry_id
        return True
    
    def build_session_context(self) -> List[Message]:
        """
        构建当前分支的消息列表（用于恢复 MMU 状态）。
        
        Returns:
            Message 列表
        """
        messages = []
        for entry in self.get_branch():
            msg = entry.to_message()
            if msg:
                messages.append(msg)
        return messages
    
    # =========================================================================
    # Labels (Bookmarks)
    # =========================================================================
    
    def set_label(self, entry_id: str, label: Optional[str]) -> bool:
        """
        设置或清除条目标签。
        
        Args:
            entry_id: 条目 ID
            label: 标签文本，None 表示清除
        
        Returns:
            是否成功
        """
        entry = self._entries_by_id.get(entry_id)
        if not entry:
            return False
        
        entry.label = label
        # TODO: 更新持久化文件（需要重写或使用 index）
        return True
    
    def get_labeled_entries(self) -> List[SessionEntry]:
        """获取所有有标签的条目"""
        return [e for e in self._entries if e.label]
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """获取会话统计"""
        branch = self.get_branch()
        
        user_count = sum(1 for e in branch if e.type == "user")
        assistant_count = sum(1 for e in branch if e.type == "assistant")
        tool_count = sum(1 for e in branch if e.type == "tool_result")
        compaction_count = sum(1 for e in branch if e.type == "compaction")
        
        return {
            "session_id": self._session_id,
            "session_file": str(self._session_file) if self._session_file else None,
            "total_entries": len(self._entries),
            "branch_length": len(branch),
            "user_messages": user_count,
            "assistant_messages": assistant_count,
            "tool_results": tool_count,
            "compactions": compaction_count,
        }
    
    # =========================================================================
    # Recent Sessions
    # =========================================================================
    
    def list_recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        列出最近的会话。
        
        Args:
            limit: 最大数量
        
        Returns:
            会话信息列表
        """
        sessions = []
        
        # 遍历所有日期目录
        if self._session_dir.exists():
            for date_dir in sorted(self._session_dir.iterdir(), reverse=True):
                if not date_dir.is_dir():
                    continue
                for session_file in sorted(date_dir.glob("*.jsonl"), reverse=True):
                    # 读取第一条消息作为预览
                    preview = ""
                    try:
                        with open(session_file, "r", encoding="utf-8") as f:
                            for line in f:
                                data = json.loads(line.strip())
                                if data.get("type") == "user":
                                    content = data.get("data", {}).get("content", "")
                                    preview = content[:100] if isinstance(content, str) else str(content)[:100]
                                    break
                    except:
                        pass
                    
                    sessions.append({
                        "id": session_file.stem,
                        "file": str(session_file),
                        "date": date_dir.name,
                        "preview": preview,
                        "mtime": session_file.stat().st_mtime,
                    })
                    
                    if len(sessions) >= limit:
                        return sessions
        
        return sessions
    
    # =========================================================================
    # Internal Methods
    # =========================================================================
    
    def _persist_entry(self, entry: SessionEntry) -> None:
        """持久化单个条目到文件"""
        if not self._session_file:
            return
        
        with open(self._session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    
    def _write_meta(self, meta: Dict[str, Any]) -> None:
        """写入元数据"""
        if not self._session_file:
            return
        
        with open(self._session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"_meta": True, **meta}, ensure_ascii=False) + "\n")


# =============================================================================
# In-Memory Session Manager (for testing / ephemeral mode)
# =============================================================================

class InMemorySessionManager(SessionManager):
    """
    内存中的会话管理器（不持久化）。
    
    用于测试或临时模式。
    """
    
    def __init__(self):
        super().__init__(session_dir=None, session_file=None)
        self._session_id = uuid.uuid4().hex[:12]
    
    def _persist_entry(self, entry: SessionEntry) -> None:
        """不持久化"""
        pass
    
    def _write_meta(self, meta: Dict[str, Any]) -> None:
        """不写入元数据"""
        pass
    
    def load_session(self, session_file: Path) -> bool:
        """不支持加载"""
        return False
