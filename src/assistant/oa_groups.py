"""
Official Account Group Manager — CRUD for OA groups

Each group contains:
- A list of OA accounts (gh_xxx)
- Schedule configuration
- Digest template (LLM prompt)
- Push target (chatroom or user)
"""
import json
import logging
import os
from dataclasses import asdict

from src.assistant.config import (
    AssistantConfig,
    OAGroup,
    load_assistant_config,
    save_assistant_config,
)

logger = logging.getLogger(__name__)


class OAGroupManager:
    """Manages Official Account groups: create, read, update, delete."""

    def __init__(self, config: AssistantConfig):
        self._config = config

    def _save(self):
        """Persist current config."""
        save_assistant_config(self._config)

    # ── CRUD ──────────────────────────────────────────────────────────

    def list_groups(self) -> list[OAGroup]:
        """List all OA groups."""
        return self._config.oa_groups

    def get_group(self, group_id: str) -> OAGroup | None:
        """Get a group by ID."""
        for g in self._config.oa_groups:
            if g.id == group_id:
                return g
        return None

    def create_group(
        self,
        name: str,
        accounts: list[str] | None = None,
        schedule: list[str] | None = None,
        cron_expr: str = "",
        digest_template: str = "default",
        push_target: str = "",
        lookback_hours: int = 24,
        lookback_mode: str = "auto",
        custom_prompt: str = "",
    ) -> OAGroup:
        """Create a new OA group.

        Args:
            name: Group display name
            accounts: List of gh_xxx account IDs
            schedule: DEPRECATED — use cron_expr instead
            cron_expr: 5-field cron expression for scheduling
            digest_template: LLM prompt template key
            push_target: Target chatroom or user wxid
            lookback_hours: Hours to look back for articles
            lookback_mode: "auto" (derive from schedule) or "manual"
            custom_prompt: Custom LLM prompt (overrides digest_template)

        Returns:
            The created OAGroup
        """
        # Generate ID
        group_id = f"grp_{len(self._config.oa_groups) + 1:03d}"
        # Ensure unique
        existing_ids = {g.id for g in self._config.oa_groups}
        while group_id in existing_ids:
            num = int(group_id.split("_")[1]) + 1
            group_id = f"grp_{num:03d}"

        group = OAGroup(
            id=group_id,
            name=name,
            accounts=accounts or [],
            schedule=[],  # Deprecated
            cron_expr=cron_expr,
            digest_template=digest_template,
            push_target=push_target,
            lookback_hours=lookback_hours,
            lookback_mode=lookback_mode,
            custom_prompt=custom_prompt,
        )

        self._config.oa_groups.append(group)
        self._save()
        logger.info("Created OA group: %s (%s) cron=%s", group.name, group.id, cron_expr)
        return group

    def update_group(self, group_id: str, **kwargs) -> OAGroup | None:
        """Update a group's fields.

        Args:
            group_id: Group ID to update
            **kwargs: Fields to update (name, accounts, schedule, etc.)

        Returns:
            Updated OAGroup or None if not found
        """
        group = self.get_group(group_id)
        if not group:
            return None

        for key, value in kwargs.items():
            if hasattr(group, key):
                setattr(group, key, value)

        self._save()
        logger.info("Updated OA group: %s", group_id)
        return group

    def delete_group(self, group_id: str) -> bool:
        """Delete a group by ID.

        Returns:
            True if deleted, False if not found
        """
        original_len = len(self._config.oa_groups)
        self._config.oa_groups = [
            g for g in self._config.oa_groups if g.id != group_id
        ]

        if len(self._config.oa_groups) < original_len:
            self._save()
            logger.info("Deleted OA group: %s", group_id)
            return True
        return False

    def add_account(self, group_id: str, gh_id: str) -> bool:
        """Add an OA account to a group."""
        group = self.get_group(group_id)
        if not group:
            return False
        if gh_id not in group.accounts:
            group.accounts.append(gh_id)
            self._save()
        return True

    def remove_account(self, group_id: str, gh_id: str) -> bool:
        """Remove an OA account from a group."""
        group = self.get_group(group_id)
        if not group:
            return False
        if gh_id in group.accounts:
            group.accounts.remove(gh_id)
            self._save()
        return True

    # ── Utility ───────────────────────────────────────────────────────

    def get_accounts_in_group(self, group_id: str) -> list[str]:
        """Get all account IDs in a group."""
        group = self.get_group(group_id)
        return group.accounts if group else []

    def get_groups_for_account(self, gh_id: str) -> list[OAGroup]:
        """Find all groups containing a specific account."""
        return [g for g in self._config.oa_groups if gh_id in g.accounts]
