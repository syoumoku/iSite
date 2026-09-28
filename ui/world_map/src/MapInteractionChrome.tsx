import type React from "react";

export type MapPointerPosition = {
  x: number;
  y: number;
};

export type MapHoverMarker = {
  title: string;
  detail: string;
  variant?: "country" | "city" | "cluster" | "property" | "network" | "footfall";
};

type MapInteractionChromeProps = {
  pointerPosition: MapPointerPosition | null;
  hoveredMarker: MapHoverMarker | null;
  stageSize: { width: number; height: number };
};

const TOOLTIP_WIDTH = 176;
const TOOLTIP_HEIGHT = 54;
const NETWORK_TOOLTIP_WIDTH = 292;
const NETWORK_TOOLTIP_HEIGHT = 78;
const FOOTFALL_TOOLTIP_WIDTH = 224;
const FOOTFALL_TOOLTIP_HEIGHT = 62;
const TOOLTIP_OFFSET_X = 22;
const TOOLTIP_OFFSET_Y = 18;
const EDGE_GAP = 14;

export default function MapInteractionChrome({
  pointerPosition,
  hoveredMarker,
  stageSize,
}: MapInteractionChromeProps) {
  if (!pointerPosition) {
    return null;
  }
  const tooltipStyle = tooltipPosition(pointerPosition, stageSize, hoveredMarker?.variant);
  return (
    <div
      className="map-interaction-chrome"
      data-hovered-marker={hoveredMarker ? "true" : "false"}
      data-cursor-variant={hoveredMarker?.variant || "idle"}
      aria-hidden="true"
    >
      <span
        className="map-custom-cursor"
        style={{
          transform: `translate3d(${pointerPosition.x}px, ${pointerPosition.y}px, 0) translate(-50%, -50%)`,
        }}
      />
      {hoveredMarker && (
        <div
          className="map-hover-tooltip"
          data-map-tooltip="true"
          data-tooltip-variant={hoveredMarker.variant || "default"}
          style={tooltipStyle}
        >
          <strong>{hoveredMarker.title}</strong>
          <span>{hoveredMarker.detail}</span>
        </div>
      )}
    </div>
  );
}

function tooltipPosition(
  pointerPosition: MapPointerPosition,
  stageSize: { width: number; height: number },
  variant?: MapHoverMarker["variant"],
): React.CSSProperties {
  const width = Math.max(stageSize.width, 320);
  const height = Math.max(stageSize.height, 320);
  const tooltipWidth = tooltipWidthForVariant(variant);
  const tooltipHeight = tooltipHeightForVariant(variant);
  let left = pointerPosition.x + TOOLTIP_OFFSET_X;
  if (left + tooltipWidth > width - EDGE_GAP) {
    left = pointerPosition.x - tooltipWidth - TOOLTIP_OFFSET_X;
  }
  let top = pointerPosition.y - tooltipHeight - TOOLTIP_OFFSET_Y;
  if (top < EDGE_GAP) {
    top = pointerPosition.y + TOOLTIP_OFFSET_Y;
  }
  if (top + tooltipHeight > height - EDGE_GAP) {
    top = height - tooltipHeight - EDGE_GAP;
  }
  return {
    transform: `translate3d(${Math.round(left)}px, ${Math.round(top)}px, 0)`,
  };
}

function tooltipWidthForVariant(variant?: MapHoverMarker["variant"]): number {
  if (variant === "network") {
    return NETWORK_TOOLTIP_WIDTH;
  }
  if (variant === "footfall") {
    return FOOTFALL_TOOLTIP_WIDTH;
  }
  return TOOLTIP_WIDTH;
}

function tooltipHeightForVariant(variant?: MapHoverMarker["variant"]): number {
  if (variant === "network") {
    return NETWORK_TOOLTIP_HEIGHT;
  }
  if (variant === "footfall") {
    return FOOTFALL_TOOLTIP_HEIGHT;
  }
  return TOOLTIP_HEIGHT;
}
