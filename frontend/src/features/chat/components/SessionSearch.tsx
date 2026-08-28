// =============================================================================
// PH Agent Hub — SessionSearch
// =============================================================================
// Ant Design Input.Search; GET /chat/sessions/search?q=; results in Ant Design List.
// =============================================================================

import { useState } from "react";
import { Input, List, Typography, Empty, Spin, Segmented, Tag, Space } from "antd";
import { useNavigate } from "react-router-dom";
import {
  searchSessions,
  listSessionsByTag,
  SessionData,
  SearchScope,
} from "../services/chat";

const { Text } = Typography;
const { Search } = Input;

const SCOPE_OPTIONS: { label: string; value: SearchScope }[] = [
  { label: "Everything", value: "all" },
  { label: "Title", value: "title" },
  { label: "Content", value: "content" },
  { label: "Tag", value: "tag" },
];

const SCOPE_LABELS: Record<SearchScope, string> = {
  all: "Everything",
  title: "Title",
  content: "Content",
  tag: "Tag",
};

interface SessionSearchProps {
  onClose?: () => void;
  /** Called when a session is selected from search results, before navigation. */
  onSelect?: (session: SessionData) => void;
}

export function SessionSearch({ onClose, onSelect }: SessionSearchProps) {
  const [results, setResults] = useState<SessionData[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [scope, setScope] = useState<SearchScope>("all");
  const navigate = useNavigate();

  const handleSearch = async (value: string) => {
    if (!value.trim()) {
      setResults([]);
      setSearched(false);
      return;
    }
    setSearching(true);
    setSearched(true);
    try {
      // #tag prefix → exact tag search (unchanged). Force the Tag scope
      // option for visual consistency.
      if (value.startsWith("#")) {
        const tagName = value.slice(1).trim();
        if (!tagName) {
          setResults([]);
          setSearching(false);
          return;
        }
        setScope("tag");
        const data = await listSessionsByTag(tagName);
        setResults(data);
      } else {
        const data = await searchSessions(value, scope);
        setResults(data);
      }
    } catch {
      setResults([]);
    }
    setSearching(false);
  };

  const handleSelect = (session: SessionData) => {
    onSelect?.(session);
    navigate(`/chat/${session.id}`);
    onClose?.();
  };

  return (
    <div style={{ padding: 16 }}>
      <Segmented
        block
        options={SCOPE_OPTIONS}
        value={scope}
        onChange={(value) => setScope(value as SearchScope)}
        style={{ marginBottom: 16 }}
      />
      <Search
        placeholder="Search sessions..."
        onSearch={handleSearch}
        allowClear
        style={{ marginBottom: 16 }}
      />
      {searching ? (
        <div style={{ textAlign: "center", padding: 32 }}>
          <Spin />
        </div>
      ) : searched && results.length === 0 ? (
        <Empty description="No sessions found" />
      ) : (
        <List
          dataSource={results}
          renderItem={(item) => (
            <List.Item
              onClick={() => handleSelect(item)}
              style={{ cursor: "pointer" }}
            >
              <List.Item.Meta
                title={item.title}
                description={
                  <Space direction="vertical" size={4}>
                    <Text type="secondary">
                      {new Date(item.updated_at).toLocaleString()}
                    </Text>
                    {item.matched_fields && item.matched_fields.length > 0 && (
                      <Space size={4}>
                        {item.matched_fields.map((f) => (
                          <Tag key={f} color="blue">
                            {SCOPE_LABELS[f as SearchScope] ?? f}
                          </Tag>
                        ))}
                      </Space>
                    )}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
}

export default SessionSearch;
