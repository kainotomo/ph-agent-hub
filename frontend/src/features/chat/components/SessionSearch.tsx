// =============================================================================
// PH Agent Hub — SessionSearch
// =============================================================================
// Ant Design Input.Search; GET /chat/sessions/search?q=; results in Ant Design List.
// =============================================================================

import { useState } from "react";
import { Input, List, Typography, Empty, Spin } from "antd";
import { useNavigate } from "react-router-dom";
import { searchSessions, listSessionsByTag, SessionData } from "../services/chat";

const { Text } = Typography;
const { Search } = Input;

interface SessionSearchProps {
  onClose?: () => void;
  /** Called when a session is selected from search results, before navigation. */
  onSelect?: (session: SessionData) => void;
}

export function SessionSearch({ onClose, onSelect }: SessionSearchProps) {
  const [results, setResults] = useState<SessionData[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
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
      // #tag prefix → search by tag name
      if (value.startsWith("#")) {
        const tagName = value.slice(1).trim();
        if (!tagName) {
          setResults([]);
          setSearching(false);
          return;
        }
        const data = await listSessionsByTag(tagName);
        setResults(data);
      } else {
        const data = await searchSessions(value);
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
                  <Text type="secondary">
                    {item.is_temporary ? "Temporary" : "Permanent"} ·{" "}
                    {new Date(item.updated_at).toLocaleString()}
                  </Text>
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
