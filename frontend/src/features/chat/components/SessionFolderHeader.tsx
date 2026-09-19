// =============================================================================
// PH Agent Hub — SessionFolderHeader (Issue #526)
// =============================================================================
// One row in the chat sidebar that represents a folder: collapse toggle,
// tinted folder icon, name, session count, "new chat here", and a menu for
// rename / change colour / delete.  The same component renders the
// always-present "Unfiled" group, where rename/delete are hidden.
//
// It is also a drop target for sessions dragged out of other folders.
// =============================================================================

import React from "react";
import { Button, Dropdown, Tooltip, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  CaretDownOutlined,
  CaretRightOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderOutlined,
  InboxOutlined,
  PlusOutlined,
  BgColorsOutlined,
  MoreOutlined,
} from "@ant-design/icons";

const { Text } = Typography;

export interface SessionFolderHeaderProps {
  id: string;
  name: string;
  color: string | null;
  count: number;
  collapsed: boolean;
  /** True for the synthetic "Unfiled" group (no rename/delete/new-chat). */
  isUnfiled: boolean;
  /** Highlight while a session is dragged over this folder. */
  isDropTarget: boolean;
  /** When true, the chevron and folder-name click are disabled (e.g. during
   *  search filtering where groups are always expanded). */
  toggleDisabled?: boolean;
  onToggle: () => void;
  onNewChatHere: () => void;
  onRename: () => void;
  onChangeColor: () => void;
  onDelete: () => void;
  onDragOver: (event: React.DragEvent<HTMLDivElement>, id: string) => void;
  onDragLeave: (event: React.DragEvent<HTMLDivElement>, id: string) => void;
  onDrop: (event: React.DragEvent<HTMLDivElement>, id: string) => void;
}

export const SessionFolderHeader = React.memo(function SessionFolderHeader({
  id,
  name,
  color,
  count,
  collapsed,
  isUnfiled,
  isDropTarget,
  toggleDisabled,
  onToggle,
  onNewChatHere,
  onRename,
  onChangeColor,
  onDelete,
  onDragOver,
  onDragLeave,
  onDrop,
}: SessionFolderHeaderProps) {
  const menuItems: MenuProps["items"] = [
    {
      key: "rename",
      icon: <EditOutlined />,
      label: "Rename",
      onClick: () => onRename(),
    },
    {
      key: "color",
      icon: <BgColorsOutlined />,
      label: "Change colour",
      onClick: () => onChangeColor(),
    },
    { type: "divider" },
    {
      key: "delete",
      icon: <DeleteOutlined />,
      label: "Delete folder",
      danger: true,
      onClick: () => onDelete(),
    },
  ];

  return (
    <div
      onDragOver={(e) => onDragOver(e, id)}
      onDragLeave={(e) => onDragLeave(e, id)}
      onDrop={(e) => onDrop(e, id)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        padding: "6px 8px 6px 10px",
        margin: "4px 6px 0",
        borderRadius: 6,
        background: isDropTarget ? "#e6f4ff" : "transparent",
        border: isDropTarget ? "1px dashed #1677ff" : "1px dashed transparent",
        cursor: "pointer",
        userSelect: "none",
      }}
    >
      <Button
        type="text"
        size="small"
        aria-label={collapsed ? `Expand ${name}` : `Collapse ${name}`}
        icon={collapsed ? <CaretRightOutlined /> : <CaretDownOutlined />}
        onClick={onToggle}
        disabled={toggleDisabled}
        style={{ flexShrink: 0, width: 18, minWidth: 18, padding: 0 }}
      />

      <span
        onClick={toggleDisabled ? undefined : onToggle}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          minWidth: 0,
          flex: 1,
          cursor: toggleDisabled ? "default" : "pointer",
        }}
      >
        {isUnfiled ? (
          <InboxOutlined style={{ flexShrink: 0, color: "#8c8c8c" }} />
        ) : (
          <FolderOutlined
            style={{ flexShrink: 0, color: color || "#1677ff" }}
          />
        )}
        <Tooltip title={name}>
          <Text
            strong
            ellipsis
            style={{ fontSize: 13, maxWidth: 150 }}
          >
            {name}
          </Text>
        </Tooltip>
        <Text type="secondary" style={{ fontSize: 11, flexShrink: 0 }}>
          {count}
        </Text>
      </span>

      {!isUnfiled && (
        <Tooltip title="New chat in this folder">
          <Button
            type="text"
            size="small"
            aria-label={`New chat in ${name}`}
            icon={<PlusOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              onNewChatHere();
            }}
          />
        </Tooltip>
      )}

      {!isUnfiled && (
        <Dropdown menu={{ items: menuItems }} trigger={["click"]}>
          <Button
            type="text"
            size="small"
            aria-label={`Folder actions for ${name}`}
            icon={<MoreOutlined />}
            onClick={(e) => e.stopPropagation()}
          />
        </Dropdown>
      )}
    </div>
  );
});

export default SessionFolderHeader;
