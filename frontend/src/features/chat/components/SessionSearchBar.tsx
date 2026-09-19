// =============================================================================
// PH Agent Hub — SessionSearchBar
// =============================================================================
// Inline search field rendered inside the sidebar (replaces the old Drawer).
// Supports scope selection (All / Title / Content / Tag), #tag exact-tag form,
// matched-field badges, and a result count indicator.
// =============================================================================

import React from "react";
import { Input, Segmented, Typography, Space } from "antd";
import type { InputRef } from "antd";
import { SearchOutlined, CloseOutlined } from "@ant-design/icons";
import type { SearchScope } from "../services/chat";

const { Text } = Typography;
const { Search } = Input;

export const SEARCH_SCOPE_LABELS: Record<SearchScope, string> = {
  all: "All",
  title: "Title",
  content: "Content",
  tag: "Tag",
};

const SCOPE_OPTIONS: { label: string; value: SearchScope }[] = [
  { label: "All", value: "all" },
  { label: "Title", value: "title" },
  { label: "Content", value: "content" },
  { label: "Tag", value: "tag" },
];

export interface SessionSearchBarProps {
  value: string;
  onChange: (value: string) => void;
  scope: SearchScope;
  onScopeChange: (scope: SearchScope) => void;
  tagMode: boolean;
  busy: boolean;
  resultCount: number;
  onClose: () => void;
  inputRef?: React.Ref<InputRef>;
}

export function SessionSearchBar({
  value,
  onChange,
  scope,
  onScopeChange,
  tagMode,
  busy,
  resultCount,
  onClose,
  inputRef,
}: SessionSearchBarProps) {
  return (
    <div
      data-testid="session-search-panel"
      style={{
        padding: "8px 12px",
        borderBottom: "1px solid #f0f0f0",
      }}
    >
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        {/* Scope Segmented */}
        <Segmented
          block
          size="small"
          options={SCOPE_OPTIONS}
          value={tagMode ? "tag" : scope}
          onChange={(value) => {
            const next = value as SearchScope;
            onScopeChange(next);
          }}
          disabled={tagMode}
          style={{ marginBottom: 0 }}
        />

        {/* Search input row */}
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <Search
            ref={inputRef}
            placeholder="Search sessions…"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
            }}
            allowClear
            aria-label="Search sessions"
            data-testid="session-search-input"
            style={{ flex: 1 }}
            prefix={<SearchOutlined />}
            suffix={
              <CloseOutlined
                style={{ cursor: "pointer", color: "#8c8c8c" }}
                onClick={onClose}
                aria-label="Close search"
              />
            }
          />
        </div>

        {/* Tag hint when in tag mode */}
        {tagMode && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            Tag search: #name
          </Text>
        )}

        {/* Status row */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {busy ? (
            <Text type="secondary" style={{ fontSize: 11 }}>
              Searching…
            </Text>
          ) : (
            <Text type="secondary" style={{ fontSize: 11 }}>
              {resultCount} result{resultCount !== 1 ? "s" : ""}
            </Text>
          )}
        </div>
      </Space>
    </div>
  );
}

export default SessionSearchBar;
