// =============================================================================
// SessionSearchBar — Unit Tests
// =============================================================================
// Covers: typing → onChange, scope Segmented → onScopeChange, tagMode pins
// Tag and shows the hint, close button → onClose, busy/count text,
// Escape → onClose.
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionSearchBar, type SessionSearchBarProps } from "./SessionSearchBar";

function renderBar(overrides: Partial<SessionSearchBarProps> = {}) {
  const props: SessionSearchBarProps = {
    value: "",
    onChange: vi.fn(),
    scope: "all",
    onScopeChange: vi.fn(),
    tagMode: false,
    busy: false,
    resultCount: 0,
    onClose: vi.fn(),
    ...overrides,
  };
  return { ...render(<SessionSearchBar {...props} />), props };
}

describe("SessionSearchBar", () => {
  it("renders the search input and scope options", () => {
    renderBar();
    expect(screen.getByPlaceholderText("Search sessions…")).toBeInTheDocument();
    expect(screen.getByText("All")).toBeInTheDocument();
    expect(screen.getByText("Title")).toBeInTheDocument();
    expect(screen.getByText("Content")).toBeInTheDocument();
    expect(screen.getByText("Tag")).toBeInTheDocument();
  });

  it("calls onChange when the input value changes", () => {
    const { props } = renderBar();
    const input = screen.getByPlaceholderText("Search sessions…");
    fireEvent.change(input, { target: { value: "hello" } });
    expect(props.onChange).toHaveBeenCalledWith("hello");
  });

  it("calls onScopeChange when a different scope is selected", async () => {
    const onScopeChange = vi.fn();
    renderBar({ onScopeChange });
    const user = userEvent.setup();

    await user.click(screen.getByText("Title"));
    expect(onScopeChange).toHaveBeenCalledWith("title");
  });

  it("shows the tag hint and ignores scope clicks when tagMode is true", async () => {
    const onScopeChange = vi.fn();
    renderBar({ value: "#work", scope: "tag", tagMode: true, onScopeChange });

    expect(screen.getByText("Tag search: #name")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByText("Title"));
    expect(onScopeChange).not.toHaveBeenCalled();

    const segmented = document.querySelector(".ant-segmented");
    expect(segmented?.className).toContain("ant-segmented-disabled");
  });

  it("shows the busy indicator while searching", () => {
    renderBar({ value: "test", busy: true });
    expect(screen.getByText("Searching…")).toBeInTheDocument();
  });

  it("shows the result count when not busy", () => {
    renderBar({ value: "test", resultCount: 5 });
    expect(screen.getByText("5 results")).toBeInTheDocument();
  });

  it("shows a singular result count", () => {
    renderBar({ value: "test", resultCount: 1 });
    expect(screen.getByText("1 result")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    renderBar({ value: "test", onClose });

    await userEvent.click(screen.getByLabelText("Close search"));
    expect(onClose).toHaveBeenCalled();
  });

  it("calls onClose on Escape", () => {
    const onClose = vi.fn();
    renderBar({ value: "test", onClose });

    const input = screen.getByPlaceholderText("Search sessions…");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
