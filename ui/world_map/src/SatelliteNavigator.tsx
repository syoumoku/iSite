import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import maplibregl, { type Map as MapLibreMap, type StyleSpecification } from "maplibre-gl";
import type { MapMouseEvent } from "maplibre-gl";

import MapInteractionChrome, {
  type MapHoverMarker,
  type MapPointerPosition,
} from "./MapInteractionChrome";
import "maplibre-gl/dist/maplibre-gl.css";

export type GeoVisualMode = "globe" | "country_satellite" | "city_satellite" | "property_satellite";
export type SatelliteTileStatus = "tiles-loading" | "tiles-ready" | "tiles-error";
export type PropertyOverlayMode = "satellite" | "mobile_network" | "footfall";
type PropertyOverlayStatus = "idle" | "loading" | "ready" | "empty" | "error";

type PropertyOverlayPayload = {
  type: "FeatureCollection";
  layer: "mobile_network" | "footfall";
  provider?: string;
  status?: string;
  message?: string;
  proxy_note?: string;
  features: Array<{
    type: "Feature";
    geometry: {
      type: "Polygon" | "Point";
      coordinates: number[][][] | number[];
    };
    properties: Record<string, string | number | null | undefined>;
  }>;
};
type PropertyOverlayFeature = PropertyOverlayPayload["features"][number];

export type SatelliteCamera = {
  center: [number, number];
  zoom: number;
  pitch: number;
  bearing: number;
};

export type SatelliteBounds = {
  minLng: number;
  minLat: number;
  maxLng: number;
  maxLat: number;
};

export type SatelliteMarker = {
  id: string;
  kind: "city" | "city_cluster" | "property" | "property_cluster";
  lat: number;
  lng: number;
  label: string;
  country?: string;
  city?: string;
  count?: number;
  selected?: boolean;
  meta?: string;
  cityIds?: string[];
  cityNames?: string[];
  cityCount?: number;
  candidateCount?: number;
  sourceCount?: number;
  reviewCount?: number;
  scenes?: Record<string, number>;
  bounds?: SatelliteBounds;
  childMarkers?: ProjectedSatelliteMarker[];
};

type ProjectedSatelliteMarker = SatelliteMarker & {
  x: number;
  y: number;
  visible: boolean;
  spiderChild?: boolean;
};

type SatelliteNavigatorProps = {
  mode: GeoVisualMode;
  camera: SatelliteCamera;
  markers: SatelliteMarker[];
  stageSize: { width: number; height: number };
  mock: boolean;
  tileTemplate: string;
  tileSize: number;
  attribution: string;
  propertyId: string;
  overlayMode: PropertyOverlayMode;
  overlayTemplate: string;
  overlayRadiusM: number;
  footfallProviderConfigured: boolean;
  onOverlayModeChange: (mode: PropertyOverlayMode) => void;
  onMarkerSelect: (marker: SatelliteMarker) => void;
};

const OVERLAY_SOURCE_ID = "property-overlay";
const NETWORK_LAYER_ID = "property-overlay-mobile-network";
const NETWORK_OUTLINE_LAYER_ID = "property-overlay-mobile-network-outline";
const NETWORK_ALERT_HALO_LAYER_ID = "property-overlay-mobile-network-alert-halo";
const NETWORK_CENTER_LAYER_ID = "property-overlay-mobile-network-centers";
const FOOTFALL_LAYER_ID = "property-overlay-footfall";
const FOOTFALL_CELL_LAYER_ID = "property-overlay-footfall-cells";
const FOOTFALL_CENTER_LAYER_ID = "property-overlay-footfall-centers";
const NETWORK_OVERLAY_LAYER_IDS = [
  NETWORK_LAYER_ID,
  NETWORK_OUTLINE_LAYER_ID,
  NETWORK_ALERT_HALO_LAYER_ID,
  NETWORK_CENTER_LAYER_ID,
];
const FOOTFALL_OVERLAY_LAYER_IDS = [
  FOOTFALL_CELL_LAYER_ID,
  FOOTFALL_LAYER_ID,
  FOOTFALL_CENTER_LAYER_ID,
];

const SATELLITE_CITY_CLUSTER_RADIUS_PX = 104;
const SATELLITE_PROPERTY_CLUSTER_RADIUS_PX = 82;
const SPIDER_BASE_RADIUS_PX = 78;
const SPIDER_RING_GAP_PX = 44;
const SPIDER_FIRST_RING_CAPACITY = 8;
const SPIDER_EDGE_PADDING_PX = 34;

export default function SatelliteNavigator({
  mode,
  camera,
  markers,
  stageSize,
  mock,
  tileTemplate,
  tileSize,
  attribution,
  propertyId,
  overlayMode,
  overlayTemplate,
  overlayRadiusM,
  footfallProviderConfigured,
  onOverlayModeChange,
  onMarkerSelect,
}: SatelliteNavigatorProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayPayloadRef = useRef<PropertyOverlayPayload | null>(null);
  const markersRef = useRef(markers);
  const [tileStatus, setTileStatus] = useState<SatelliteTileStatus>("tiles-loading");
  const [projectedMarkers, setProjectedMarkers] = useState<ProjectedSatelliteMarker[]>([]);
  const [expandedSatelliteClusterKey, setExpandedSatelliteClusterKey] = useState("");
  const [spiderSatelliteClusterKey, setSpiderSatelliteClusterKey] = useState("");
  const [pointerPosition, setPointerPosition] = useState<MapPointerPosition | null>(null);
  const [hoveredMarker, setHoveredMarker] = useState<MapHoverMarker | null>(null);
  const [overlayStatus, setOverlayStatus] = useState<PropertyOverlayStatus>("idle");
  const [overlayMessage, setOverlayMessage] = useState("");
  const [overlayHover, setOverlayHover] = useState<MapHoverMarker | null>(null);
  const [overlayLayerReadyKey, setOverlayLayerReadyKey] = useState("");
  const markerSignature = useMemo(
    () => markers.map((marker) => `${marker.kind}:${marker.id}`).join("|"),
    [markers],
  );
  const updateOverlayHoverAtPoint = useCallback((point: MapPointerPosition): boolean => {
    const map = mapRef.current;
    if (mock || !map || mode !== "property_satellite" || overlayMode === "satellite") {
      return false;
    }
    const interactiveLayers = overlayInteractiveLayerIds(map, overlayMode);
    if (!interactiveLayers.length) {
      return false;
    }
    const feature = (
      map.queryRenderedFeatures([point.x, point.y], { layers: interactiveLayers })[0]
      || overlayFeatureAtPoint(map, overlayPayloadRef.current, overlayMode, point)
    );
    if (!feature?.properties) {
      setOverlayHover(null);
      return false;
    }
    setOverlayHover(
      overlayMode === "mobile_network"
        ? networkOverlayHoverInfo(feature.properties)
        : footfallOverlayHoverInfo(feature.properties),
    );
    return true;
  }, [mock, mode, overlayLayerReadyKey, overlayMode]);

  useEffect(() => {
    markersRef.current = markers;
  }, [markers]);

  useEffect(() => {
    setExpandedSatelliteClusterKey("");
    setSpiderSatelliteClusterKey("");
  }, [markerSignature, mode]);

  const refreshProjectedMarkers = useCallback(() => {
    const map = mapRef.current;
    const container = containerRef.current;
    if (!map || !container) {
      setProjectedMarkers([]);
      return;
    }
    const rect = container.getBoundingClientRect();
    setProjectedMarkers(
      markersRef.current.map((marker) => {
        const point = map.project([marker.lng, marker.lat]);
        return {
          ...marker,
          x: point.x,
          y: point.y,
          visible:
            point.x >= -48
            && point.x <= rect.width + 48
            && point.y >= -48
            && point.y <= rect.height + 48,
        };
      }),
    );
  }, []);

  useEffect(() => {
    if (mock) {
      setProjectedMarkers(projectMockSatelliteMarkers(markers, camera, stageSize));
      return;
    }
    setProjectedMarkers([]);
  }, [camera, markers, mock, stageSize]);

  useEffect(() => {
    if (!mock) {
      maplibregl.prewarm();
    }
  }, [mock]);

  useEffect(() => {
    if (mock || !containerRef.current || mapRef.current) {
      return;
    }
    setTileStatus("tiles-loading");
    const style: StyleSpecification = {
      version: 8,
      sources: {
        satellite: {
          type: "raster",
          tiles: [tileTemplate],
          tileSize,
          attribution,
        },
      },
      layers: [
        {
          id: "satellite",
          type: "raster",
          source: "satellite",
          paint: {
            "raster-brightness-min": 0.08,
            "raster-brightness-max": 0.86,
            "raster-contrast": 0.18,
            "raster-saturation": -0.16,
          },
        },
      ],
    };
    const map = new maplibregl.Map({
      container: containerRef.current,
      style,
      center: camera.center,
      zoom: camera.zoom,
      pitch: camera.pitch,
      bearing: camera.bearing,
      attributionControl: { compact: true },
      maxPitch: 68,
    });
    mapRef.current = map;
    const update = () => refreshProjectedMarkers();
    const markReady = () => setTileStatus((current) => current === "tiles-error" ? current : "tiles-ready");
    map.on("load", update);
    map.on("move", update);
    map.on("resize", update);
    map.on("idle", markReady);
    map.on("error", () => setTileStatus("tiles-error"));
    return () => {
      map.off("load", update);
      map.off("move", update);
      map.off("resize", update);
      map.off("idle", markReady);
      map.remove();
      mapRef.current = null;
    };
  }, [attribution, camera.bearing, camera.center, camera.pitch, camera.zoom, mock, refreshProjectedMarkers, tileSize, tileTemplate]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || mock) {
      return;
    }
    setTileStatus("tiles-loading");
    map.easeTo({
      center: camera.center,
      zoom: camera.zoom,
      pitch: camera.pitch,
      bearing: camera.bearing,
      duration: 850,
      essential: true,
    });
    const timer = window.setTimeout(refreshProjectedMarkers, 900);
    return () => window.clearTimeout(timer);
  }, [camera, mock, refreshProjectedMarkers]);

  useEffect(() => {
    if (mock) {
      return;
    }
    prefetchSatelliteTiles(tileTemplate, camera, mode);
  }, [camera, mock, mode, tileTemplate]);

  useEffect(() => {
    if (mode !== "property_satellite" || overlayMode === "satellite" || !propertyId || !overlayTemplate) {
      setOverlayStatus("idle");
      setOverlayMessage("");
      setOverlayHover(null);
      overlayPayloadRef.current = null;
      clearPropertyOverlay(mapRef.current);
      return;
    }
    const map = mapRef.current;
    if (!map && !mock) {
      return;
    }
    const controller = new AbortController();
    setOverlayStatus("loading");
    setOverlayMessage("");
    const url = propertyOverlayUrl(overlayTemplate, propertyId, overlayMode, overlayRadiusM);
    fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`);
        }
        return response.json() as Promise<PropertyOverlayPayload>;
      })
      .then((payload) => {
        if (controller.signal.aborted) {
          return;
        }
        if (!payload.features?.length) {
          overlayPayloadRef.current = null;
          clearPropertyOverlay(map);
          setOverlayStatus("empty");
          setOverlayMessage(payload.message || propertyOverlayEmptyMessage(overlayMode, footfallProviderConfigured));
          return;
        }
        overlayPayloadRef.current = payload;
        if (map) {
          applyPropertyOverlay(map, payload, overlayMode, () => {
            if (controller.signal.aborted) {
              return;
            }
            setOverlayLayerReadyKey(`${overlayMode}:${propertyId}:${payload.features.length}`);
            setOverlayStatus("ready");
            setOverlayMessage(payload.proxy_note || payload.message || "");
          });
          return;
        }
        setOverlayLayerReadyKey(`${overlayMode}:${propertyId}:${payload.features.length}`);
        setOverlayStatus("ready");
        setOverlayMessage(payload.proxy_note || payload.message || "");
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return;
        }
        clearPropertyOverlay(map);
        overlayPayloadRef.current = null;
        setOverlayStatus("error");
        setOverlayMessage(error instanceof Error ? error.message : "Overlay request failed.");
      });
    return () => {
      controller.abort();
    };
  }, [
    footfallProviderConfigured,
    mock,
    mode,
    overlayMode,
    overlayRadiusM,
    overlayTemplate,
    propertyId,
  ]);

  useEffect(() => {
    if (mode !== "property_satellite") {
      setOverlayHover(null);
      overlayPayloadRef.current = null;
      clearPropertyOverlay(mapRef.current);
    }
  }, [mode]);

  useEffect(() => {
    const map = mapRef.current;
    if (mock || !map || mode !== "property_satellite") {
      return;
    }
    const handleOverlayMove = (event: MapMouseEvent) => {
      const pointer = {
        x: event.point.x,
        y: event.point.y,
      };
      setPointerPosition(pointer);
      if (!updateOverlayHoverAtPoint(pointer)) {
        map.getCanvas().style.cursor = "";
        return;
      }
      map.getCanvas().style.cursor = "none";
    };
    const handleLeave = () => {
      map.getCanvas().style.cursor = "";
      setOverlayHover(null);
    };
    map.on("mousemove", handleOverlayMove);
    map.on("mouseleave", handleLeave);
    return () => {
      map.off("mousemove", handleOverlayMove);
      map.off("mouseleave", handleLeave);
      map.getCanvas().style.cursor = "";
    };
  }, [mock, mode, overlayLayerReadyKey, overlayMode, updateOverlayHoverAtPoint]);

  useEffect(() => {
    if (mock || !mapRef.current) {
      return;
    }
    refreshProjectedMarkers();
  }, [markers, mock, refreshProjectedMarkers]);

  const rawVisibleMarkers = mock ? projectMockSatelliteMarkers(markers, camera, stageSize) : projectedMarkers;
  const visibleMarkers = useMemo(
    () => clusterProjectedSatelliteMarkers(rawVisibleMarkers, {
      expandedClusterKey: expandedSatelliteClusterKey,
      mode,
      spiderClusterKey: spiderSatelliteClusterKey,
      stageSize,
    }),
    [expandedSatelliteClusterKey, mode, rawVisibleMarkers, spiderSatelliteClusterKey, stageSize],
  );
  const handleMarkerSelect = useCallback((marker: ProjectedSatelliteMarker) => {
    if (!isSatelliteClusterMarker(marker)) {
      onMarkerSelect(marker);
      return;
    }
    if (expandedSatelliteClusterKey === marker.id) {
      setSpiderSatelliteClusterKey(marker.id);
      return;
    }
    setExpandedSatelliteClusterKey(marker.id);
    setSpiderSatelliteClusterKey("");
    const bounds = marker.bounds;
    if (!bounds || mock) {
      return;
    }
    const map = mapRef.current;
    if (!map) {
      return;
    }
    const isPropertyCluster = marker.kind === "property_cluster";
    const nextCamera = cameraFromBounds(bounds, {
      pitch: camera.pitch,
      bearing: camera.bearing,
      minZoom: isPropertyCluster
        ? Math.min(15.2, Math.max(camera.zoom + 0.9, 11.8))
        : Math.min(8.4, Math.max(camera.zoom + 0.9, 5.8)),
      maxZoom: isPropertyCluster ? 17.2 : 9.8,
    });
    map.easeTo({
      center: nextCamera.center,
      zoom: nextCamera.zoom,
      pitch: nextCamera.pitch,
      bearing: nextCamera.bearing,
      duration: 900,
      essential: true,
    });
    window.setTimeout(refreshProjectedMarkers, 950);
  }, [
    camera.bearing,
    camera.pitch,
    camera.zoom,
    expandedSatelliteClusterKey,
    mock,
    onMarkerSelect,
    refreshProjectedMarkers,
  ]);
  const handlePointerMove = useCallback((event: PointerEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const pointer = {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
    setPointerPosition(pointer);
    const target = event.target instanceof Element ? event.target : null;
    const isOverlayChrome = Boolean(target?.closest(".property-overlay-console, .network-overlay-legend, .footfall-overlay-legend"));
    if (isOverlayChrome) {
      setOverlayHover(null);
      return;
    }
    updateOverlayHoverAtPoint(pointer);
  }, [updateOverlayHoverAtPoint]);
  const handlePointerLeave = useCallback(() => {
    setPointerPosition(null);
    setHoveredMarker(null);
    setOverlayHover(null);
  }, []);
  const modeLabel = satelliteModeLabel(mode);
  const activeHover = overlayHover || hoveredMarker;

  return (
    <div
      className={`geo-visual-layer satellite-navigator map-interaction-hotzone ${mock ? "mock-satellite" : ""}`}
      aria-label="Satellite opportunity map"
      data-satellite-mode={mode}
      data-tile-status={mock ? "tiles-ready" : tileStatus}
      data-expanded-satellite-cluster={expandedSatelliteClusterKey}
      data-spider-satellite-cluster={spiderSatelliteClusterKey}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
    >
      <div ref={containerRef} className="satellite-map-surface" aria-hidden={mock ? "true" : "false"} />
      {mock && <canvas className="satellite-mock-canvas" width={stageSize.width} height={stageSize.height} />}
      <div className="satellite-shade" aria-hidden="true" />
      {(tileStatus !== "tiles-ready" || mock) && (
        <div className="satellite-status">
          <span>{modeLabel}</span>
          {!mock && tileStatus === "tiles-loading" && <strong>Loading imagery</strong>}
          {!mock && tileStatus === "tiles-error" && <strong>Imagery fallback active</strong>}
        </div>
      )}
      {mode === "property_satellite" && (
        <PropertyOverlayControls
          activeMode={overlayMode}
          status={overlayStatus}
          message={overlayMessage}
          footfallProviderConfigured={footfallProviderConfigured}
          onChange={onOverlayModeChange}
        />
      )}
      {mode === "property_satellite" && overlayMode === "mobile_network" && overlayStatus === "ready" && (
        <NetworkOverlayLegend />
      )}
      {mode === "property_satellite" && overlayMode === "footfall" && overlayStatus === "ready" && (
        <FootfallOverlayLegend />
      )}
      <div className="satellite-marker-layer" aria-label="Satellite marker layer">
        {visibleMarkers.map((marker) => (
          <SatelliteMarkerButton
            key={`${marker.kind}-${marker.id}`}
            marker={marker}
            onSelect={handleMarkerSelect}
            onHover={setHoveredMarker}
          />
        ))}
      </div>
      <MapInteractionChrome
        pointerPosition={pointerPosition}
        hoveredMarker={activeHover}
        stageSize={stageSize}
      />
    </div>
  );
}

function SatelliteMarkerButton({
  marker,
  onSelect,
  onHover,
}: {
  marker: ProjectedSatelliteMarker;
  onSelect: (marker: ProjectedSatelliteMarker) => void;
  onHover: (marker: MapHoverMarker | null) => void;
}) {
  const showCount = satelliteMarkerShowsCount(marker);
  return (
    <button
      className={`satellite-marker satellite-marker-${marker.kind.replace("_", "-")} ${marker.selected ? "active" : ""} ${marker.spiderChild ? "spider-child" : ""}`}
      type="button"
      aria-label={satelliteMarkerAriaLabel(marker)}
      data-marker-detail={satelliteMarkerTitle(marker)}
      data-satellite-marker-kind={marker.kind}
      onMouseEnter={() => onHover(satelliteMarkerHoverInfo(marker))}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(satelliteMarkerHoverInfo(marker))}
      onBlur={() => onHover(null)}
      onClick={() => onSelect(marker)}
      style={{
        opacity: marker.visible ? 1 : 0,
        pointerEvents: marker.visible ? "auto" : "none",
        transform: `translate3d(${marker.x}px, ${marker.y}px, 0) translate(-50%, -50%)`,
      }}
    >
      <span className="satellite-marker-pulse" aria-hidden="true" />
      <span className="satellite-marker-core">
        {showCount
          ? formatNumber(marker.count ?? marker.candidateCount ?? marker.cityCount ?? 0)
          : <span className="satellite-marker-pin" aria-hidden="true" />}
      </span>
      <span className="satellite-marker-label">
        {marker.label}
        {marker.meta && <small>{marker.meta}</small>}
      </span>
    </button>
  );
}

function PropertyOverlayControls({
  activeMode,
  status,
  message,
  footfallProviderConfigured,
  onChange,
}: {
  activeMode: PropertyOverlayMode;
  status: PropertyOverlayStatus;
  message: string;
  footfallProviderConfigured: boolean;
  onChange: (mode: PropertyOverlayMode) => void;
}) {
  const options: Array<{ mode: PropertyOverlayMode; label: string; detail: string }> = [
    { mode: "satellite", label: "Satellite", detail: "Imagery only" },
    { mode: "mobile_network", label: "Network", detail: "Ookla mobile" },
    { mode: "footfall", label: "Footfall", detail: "Public data" },
  ];
  return (
    <div className="property-overlay-console" aria-label="Property map overlay controls">
      <div className="property-overlay-buttons">
        {options.map((option) => (
          <button
            key={option.mode}
            type="button"
            className={activeMode === option.mode ? "active" : ""}
            data-overlay-mode={option.mode}
            onClick={() => onChange(option.mode)}
          >
            <span>{option.label}</span>
            <small>{option.detail}</small>
          </button>
        ))}
      </div>
      {activeMode !== "satellite" && (
        <div
          className={`property-overlay-status property-overlay-status-${status}`}
          role={status === "loading" ? "status" : undefined}
          aria-live={status === "loading" ? "polite" : undefined}
        >
          <strong>{status === "loading" ? "AI Thinking" : propertyOverlayStatusLabel(status)}</strong>
          <span>
            {status === "loading"
              ? "Loading property-scale data layer"
              : propertyOverlayStatusMessage(activeMode, status, message, footfallProviderConfigured)}
          </span>
        </div>
      )}
    </div>
  );
}

function NetworkOverlayLegend() {
  return (
    <div className="network-overlay-legend" aria-label="Network experience legend">
      <div className="network-overlay-legend-header">
        <strong>Network Experience</strong>
        <span>5 km · Ookla mobile z16</span>
      </div>
      <div className="network-overlay-legend-gradient" aria-hidden="true" />
      <div className="network-overlay-legend-scale">
        <span>Poor</span>
        <span>Moderate</span>
        <span>Good</span>
        <span>Excellent</span>
      </div>
      <p>Color blends download speed, latency and relative performance. Cyan dots mark tile centers; hover tiles or dots for values.</p>
    </div>
  );
}

function FootfallOverlayLegend() {
  return (
    <div className="network-overlay-legend footfall-overlay-legend" aria-label="Footfall observation legend">
      <div className="network-overlay-legend-header">
        <strong>Footfall</strong>
        <span>5 km · public observations</span>
      </div>
      <div className="footfall-overlay-legend-gradient" aria-hidden="true" />
      <div className="network-overlay-legend-scale">
        <span>Low</span>
        <span>Active</span>
        <span>Dense</span>
        <span>Peak</span>
      </div>
      <p>Heat uses public pedestrian counters or official transport-hub visitor counts. White dots mark observation centers; hover for values and source.</p>
    </div>
  );
}

function projectMockSatelliteMarkers(
  markers: SatelliteMarker[],
  camera: SatelliteCamera,
  stageSize: { width: number; height: number },
): ProjectedSatelliteMarker[] {
  const width = Math.max(320, stageSize.width);
  const height = Math.max(320, stageSize.height);
  const scale = Math.pow(2, camera.zoom - 4);
  return markers.map((marker, index) => {
    const ring = index % 7;
    const lngDelta = normalizeLongitudeDelta(marker.lng - camera.center[0]);
    const latDelta = marker.lat - camera.center[1];
    const x = clamp(width / 2 + lngDelta * 18 * scale + (ring - 3) * 10, 54, width - 54);
    const y = clamp(height / 2 - latDelta * 22 * scale + ((index % 5) - 2) * 8, 78, height - 58);
    return {
      ...marker,
      x,
      y,
      visible: true,
    };
  });
}

function clusterProjectedSatelliteMarkers(
  projected: ProjectedSatelliteMarker[],
  {
    expandedClusterKey,
    mode,
    spiderClusterKey,
    stageSize,
  }: {
    expandedClusterKey: string;
    mode: GeoVisualMode;
    spiderClusterKey: string;
    stageSize: { width: number; height: number };
  },
): ProjectedSatelliteMarker[] {
  const clusterTarget = satelliteClusterTargetForMode(mode);
  if (!clusterTarget) {
    return projected;
  }

  const passthrough: ProjectedSatelliteMarker[] = [];
  const clusterableMarkers: ProjectedSatelliteMarker[] = [];
  projected.forEach((marker) => {
    if (marker.kind !== clusterTarget.markerKind || !marker.visible || marker.selected) {
      passthrough.push(marker);
      return;
    }
    clusterableMarkers.push(marker);
  });

  const clustered: ProjectedSatelliteMarker[] = [];
  buildSatelliteClusters(clusterableMarkers, clusterTarget.radiusPx).forEach((cluster) => {
    if (cluster.length === 1) {
      clustered.push(cluster[0]);
      return;
    }
    const clusterMarker = clusterTarget.clusterKind === "city_cluster"
      ? makeSatelliteCityClusterMarker(cluster, expandedClusterKey)
      : makeSatellitePropertyClusterMarker(cluster, expandedClusterKey);
    if (clusterMarker.id === spiderClusterKey) {
      clustered.push(...spiderSatelliteCluster(cluster, clusterMarker, stageSize));
      return;
    }
    clustered.push(clusterMarker);
  });

  return [...passthrough, ...clustered];
}

function satelliteClusterTargetForMode(mode: GeoVisualMode): {
  markerKind: SatelliteMarker["kind"];
  clusterKind: "city_cluster" | "property_cluster";
  radiusPx: number;
} | null {
  if (mode === "country_satellite") {
    return {
      markerKind: "city",
      clusterKind: "city_cluster",
      radiusPx: SATELLITE_CITY_CLUSTER_RADIUS_PX,
    };
  }
  if (mode === "city_satellite") {
    return {
      markerKind: "property",
      clusterKind: "property_cluster",
      radiusPx: SATELLITE_PROPERTY_CLUSTER_RADIUS_PX,
    };
  }
  return null;
}

function buildSatelliteClusters(
  markers: ProjectedSatelliteMarker[],
  radiusPx: number,
): ProjectedSatelliteMarker[][] {
  const clusters: ProjectedSatelliteMarker[][] = [];
  markers.forEach((marker) => {
    let closestClusterIndex = -1;
    let closestDistance = Number.POSITIVE_INFINITY;
    clusters.forEach((cluster, index) => {
      const center = satelliteProjectedClusterCenter(cluster);
      const distance = screenDistance(marker, center);
      if (distance <= radiusPx && distance < closestDistance) {
        closestClusterIndex = index;
        closestDistance = distance;
      }
    });

    if (closestClusterIndex >= 0) {
      clusters[closestClusterIndex].push(marker);
      return;
    }
    clusters.push([marker]);
  });
  return clusters;
}

function makeSatellitePropertyClusterMarker(
  cluster: ProjectedSatelliteMarker[],
  expandedClusterKey: string,
): ProjectedSatelliteMarker {
  const center = satelliteProjectedClusterCenter(cluster);
  const propertyIds = cluster.map((marker) => marker.id).sort();
  const scenes: Record<string, number> = {};
  cluster.forEach((marker) => {
    Object.entries(marker.scenes || {}).forEach(([scene, count]) => {
      scenes[scene] = (scenes[scene] || 0) + count;
    });
  });
  const candidateCount = cluster.reduce((total, marker) => total + (marker.candidateCount ?? marker.count ?? 1), 0);
  const sourceCount = cluster.reduce((total, marker) => total + (marker.sourceCount || 0), 0);
  const reviewCount = cluster.reduce((total, marker) => total + (marker.reviewCount || 0), 0);
  const bounds = satelliteClusterBounds(cluster);
  const id = `satellite_property_cluster::${propertyIds.join("|")}`;
  const topScene = Object.entries(scenes).sort((a, b) => b[1] - a[1])[0]?.[0];
  return {
    id,
    kind: "property_cluster",
    lat: average(cluster.map((marker) => marker.lat)),
    lng: average(cluster.map((marker) => marker.lng)),
    label: `${formatNumber(candidateCount)} sites`,
    country: cluster[0]?.country || "",
    city: cluster[0]?.city || "",
    count: candidateCount,
    selected: id === expandedClusterKey,
    meta: topScene ? sceneLabel(topScene) : "Property cluster",
    candidateCount,
    sourceCount,
    reviewCount,
    scenes,
    bounds,
    childMarkers: cluster,
    x: center.x,
    y: center.y,
    visible: true,
  };
}

function makeSatelliteCityClusterMarker(
  cluster: ProjectedSatelliteMarker[],
  expandedClusterKey: string,
): ProjectedSatelliteMarker {
  const center = satelliteProjectedClusterCenter(cluster);
  const cityIds = cluster.map((marker) => marker.id).sort();
  const scenes: Record<string, number> = {};
  cluster.forEach((marker) => {
    Object.entries(marker.scenes || {}).forEach(([scene, count]) => {
      scenes[scene] = (scenes[scene] || 0) + count;
    });
  });
  const candidateCount = cluster.reduce((total, marker) => total + (marker.candidateCount ?? marker.count ?? 0), 0);
  const sourceCount = cluster.reduce((total, marker) => total + (marker.sourceCount || 0), 0);
  const reviewCount = cluster.reduce((total, marker) => total + (marker.reviewCount || 0), 0);
  const bounds = satelliteClusterBounds(cluster);
  const id = `satellite_city_cluster::${cityIds.join("|")}`;
  return {
    id,
    kind: "city_cluster",
    lat: average(cluster.map((marker) => marker.lat)),
    lng: average(cluster.map((marker) => marker.lng)),
    label: `${formatNumber(cluster.length)} cities`,
    country: cluster[0]?.country || "",
    count: cluster.length,
    selected: id === expandedClusterKey,
    meta: `${formatNumber(candidateCount)} candidates`,
    cityIds,
    cityNames: cluster.map((marker) => marker.city || marker.label).sort(),
    cityCount: cluster.length,
    candidateCount,
    sourceCount,
    reviewCount,
    scenes,
    bounds,
    childMarkers: cluster,
    x: center.x,
    y: center.y,
    visible: true,
  };
}

function spiderSatelliteCluster(
  cluster: ProjectedSatelliteMarker[],
  clusterMarker: ProjectedSatelliteMarker,
  stageSize: { width: number; height: number },
): ProjectedSatelliteMarker[] {
  return cluster.map((marker, index) => {
    const offset = spiderOffset(index, cluster.length);
    return {
      ...marker,
      x: roundScreenCoordinate(clampScreenX(clusterMarker.x + offset.x, stageSize.width)),
      y: roundScreenCoordinate(clampScreenY(clusterMarker.y + offset.y, stageSize.height)),
      visible: true,
      spiderChild: true,
    };
  });
}

function satelliteClusterBounds(cluster: ProjectedSatelliteMarker[]): SatelliteBounds | undefined {
  return boundsFromCoordinates(cluster.map((marker) => ({ lat: marker.lat, lng: marker.lng }))) || undefined;
}

function satelliteProjectedClusterCenter(cluster: ProjectedSatelliteMarker[]): { x: number; y: number } {
  return {
    x: roundScreenCoordinate(average(cluster.map((marker) => marker.x))),
    y: roundScreenCoordinate(average(cluster.map((marker) => marker.y))),
  };
}

function prefetchSatelliteTiles(
  tileTemplate: string,
  camera: SatelliteCamera,
  mode: GeoVisualMode,
) {
  const zoom = mode === "property_satellite"
    ? clamp(Math.round(camera.zoom), 15, 18)
    : clamp(Math.round(camera.zoom), 4, 6);
  const center = satelliteTileCoordinate(camera.center[0], camera.center[1], zoom);
  const radius = mode === "property_satellite" ? 1 : 0;
  for (let dx = -radius; dx <= radius; dx += 1) {
    for (let dy = -radius; dy <= radius; dy += 1) {
      const x = center.x + dx;
      const y = center.y + dy;
      const maxIndex = (2 ** zoom) - 1;
      if (x < 0 || y < 0 || x > maxIndex || y > maxIndex) {
        continue;
      }
      const url = formatSatelliteTileUrl(tileTemplate, zoom, y, x);
      window.setTimeout(() => {
        void fetch(url, { cache: "force-cache" }).catch(() => undefined);
      }, 0);
    }
  }
}

function satelliteMarkerAriaLabel(marker: SatelliteMarker): string {
  if (marker.kind === "city_cluster") {
    return `${formatNumber(marker.cityCount || marker.count || 0)} clustered cities in ${marker.country || "country"}, ${formatNumber(marker.candidateCount || 0)} candidate properties, click to zoom or expand`;
  }
  if (marker.kind === "property_cluster") {
    const location = [marker.city, marker.country].filter(Boolean).join(", ") || "city";
    return `${formatNumber(marker.candidateCount || marker.count || 0)} clustered property markers in ${location}, click to zoom or expand`;
  }
  if (marker.kind === "city") {
    return `${marker.city || marker.label}, ${marker.country || "country"}, ${formatNumber(marker.candidateCount ?? marker.count ?? 0)} candidate properties, city satellite marker`;
  }
  return `${marker.label}, property satellite marker`;
}

function satelliteMarkerTitle(marker: SatelliteMarker): string {
  if (marker.kind === "city_cluster") {
    const topCities = (marker.cityNames || []).slice(0, 4).join(" · ");
    const topScenes = Object.entries(marker.scenes || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 2)
      .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
      .join(" · ");
    return [
      `${formatNumber(marker.cityCount || marker.count || 0)} cities in ${marker.country || "country"}`,
      `${formatNumber(marker.candidateCount || 0)} candidates`,
      `${formatNumber(marker.reviewCount || 0)} review`,
      `${formatNumber(marker.sourceCount || 0)} sources`,
      topCities,
      topScenes,
      "city cluster satellite marker",
      "Click to zoom; click again to expand city markers",
    ].filter(Boolean).join("\n");
  }
  if (marker.kind === "property_cluster") {
    const topScenes = Object.entries(marker.scenes || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
      .join(" · ");
    return [
      `${formatNumber(marker.candidateCount || marker.count || 0)} properties`,
      [marker.city, marker.country].filter(Boolean).join(", "),
      `${formatNumber(marker.reviewCount || 0)} review`,
      `${formatNumber(marker.sourceCount || 0)} sources`,
      topScenes,
      "property cluster satellite marker",
      "Click to zoom; click again to expand property markers",
    ].filter(Boolean).join("\n");
  }
  if (marker.kind === "city") {
    const topScenes = Object.entries(marker.scenes || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 2)
      .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
      .join(" · ");
    return [
      `${marker.city || marker.label}, ${marker.country || ""}`.trim(),
      `${formatNumber(marker.candidateCount ?? marker.count ?? 0)} candidates`,
      `${formatNumber(marker.reviewCount || 0)} review`,
      `${formatNumber(marker.sourceCount || 0)} sources`,
      topScenes,
      "city satellite marker",
    ].filter(Boolean).join("\n");
  }
  return satelliteMarkerAriaLabel(marker);
}

function satelliteMarkerHoverInfo(marker: SatelliteMarker): MapHoverMarker {
  if (marker.kind === "city_cluster") {
    return {
      title: `${formatNumber(marker.cityCount || marker.count || 0)} cities`,
      detail: `${formatNumber(marker.candidateCount || 0)} candidates`,
      variant: "cluster",
    };
  }
  if (marker.kind === "property_cluster") {
    return {
      title: marker.label || "Property cluster",
      detail: `${formatNumber(marker.candidateCount || marker.count || 0)} sites`,
      variant: "cluster",
    };
  }
  if (marker.kind === "city") {
    return {
      title: marker.city || marker.label,
      detail: `${formatNumber(marker.candidateCount ?? marker.count ?? 0)} candidates`,
      variant: "city",
    };
  }
  return {
    title: marker.label,
    detail: "1 site",
    variant: "property",
  };
}

function isSatelliteClusterMarker(marker: SatelliteMarker): boolean {
  return marker.kind === "city_cluster" || marker.kind === "property_cluster";
}

function satelliteMarkerShowsCount(marker: SatelliteMarker): boolean {
  if (marker.kind === "property") {
    return (marker.count ?? marker.candidateCount ?? 1) > 1;
  }
  return true;
}

function cameraFromBounds(
  bounds: SatelliteBounds,
  config: { pitch: number; bearing: number; minZoom: number; maxZoom: number },
): SatelliteCamera {
  const lngSpan = Math.max(0.01, Math.abs(bounds.maxLng - bounds.minLng));
  const latSpan = Math.max(0.01, Math.abs(bounds.maxLat - bounds.minLat));
  const span = Math.max(lngSpan, latSpan);
  const zoom = clamp(8.2 - Math.log2(span), config.minZoom, config.maxZoom);
  return {
    center: [(bounds.minLng + bounds.maxLng) / 2, (bounds.minLat + bounds.maxLat) / 2],
    zoom,
    pitch: config.pitch,
    bearing: config.bearing,
  };
}

function boundsFromCoordinates(points: Array<{ lat: number; lng: number }>): SatelliteBounds | null {
  const usable = points.filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lng));
  if (usable.length === 0) {
    return null;
  }
  return usable.reduce<SatelliteBounds>(
    (bounds, point) => ({
      minLng: Math.min(bounds.minLng, point.lng),
      minLat: Math.min(bounds.minLat, point.lat),
      maxLng: Math.max(bounds.maxLng, point.lng),
      maxLat: Math.max(bounds.maxLat, point.lat),
    }),
    {
      minLng: usable[0].lng,
      minLat: usable[0].lat,
      maxLng: usable[0].lng,
      maxLat: usable[0].lat,
    },
  );
}

function spiderOffset(index: number, total: number): { x: number; y: number } {
  let ring = 1;
  let ringStart = 0;
  let capacity = SPIDER_FIRST_RING_CAPACITY;
  while (index >= ringStart + capacity) {
    ringStart += capacity;
    ring += 1;
    capacity = SPIDER_FIRST_RING_CAPACITY + (ring - 1) * 6;
  }

  const itemsInRing = Math.min(capacity, total - ringStart);
  const slot = index - ringStart;
  const radius = SPIDER_BASE_RADIUS_PX + (ring - 1) * SPIDER_RING_GAP_PX;
  const angle = -Math.PI / 2 + slot * (Math.PI * 2 / itemsInRing) + (ring % 2 === 0 ? Math.PI / itemsInRing : 0);
  return {
    x: Math.cos(angle) * radius,
    y: Math.sin(angle) * radius,
  };
}

function screenDistance(
  marker: { x: number; y: number },
  center: { x: number; y: number },
): number {
  return Math.hypot(marker.x - center.x, marker.y - center.y);
}

function average(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function clampScreenX(value: number, width: number): number {
  return clamp(value, SPIDER_EDGE_PADDING_PX, Math.max(SPIDER_EDGE_PADDING_PX, width - SPIDER_EDGE_PADDING_PX));
}

function clampScreenY(value: number, height: number): number {
  return clamp(value, SPIDER_EDGE_PADDING_PX, Math.max(SPIDER_EDGE_PADDING_PX, height - SPIDER_EDGE_PADDING_PX));
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function roundScreenCoordinate(value: number): number {
  return Math.round(value * 10) / 10;
}

function normalizeLongitudeDelta(delta: number): number {
  if (delta > 180) {
    return delta - 360;
  }
  if (delta < -180) {
    return delta + 360;
  }
  return delta;
}

function formatSatelliteTileUrl(template: string, z: number, y: number, x: number): string {
  return template
    .replaceAll("{z}", String(z))
    .replaceAll("{y}", String(y))
    .replaceAll("{x}", String(x));
}

function satelliteTileCoordinate(lng: number, lat: number, zoom: number): { x: number; y: number } {
  const latRad = clamp(lat, -85.05112878, 85.05112878) * Math.PI / 180;
  const scale = 2 ** zoom;
  const x = Math.floor(((lng + 180) / 360) * scale);
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale,
  );
  return {
    x: clamp(x, 0, scale - 1),
    y: clamp(y, 0, scale - 1),
  };
}

function satelliteModeLabel(mode: GeoVisualMode): string {
  if (mode === "property_satellite") {
    return "Street-level satellite";
  }
  if (mode === "city_satellite") {
    return "City satellite";
  }
  return "Country satellite";
}

function propertyOverlayUrl(
  template: string,
  propertyId: string,
  layer: PropertyOverlayMode,
  radiusM: number,
): string {
  return template
    .replaceAll("{property_id}", encodeURIComponent(propertyId))
    .replaceAll("{layer}", encodeURIComponent(layer))
    .replaceAll("{radius_m}", encodeURIComponent(String(radiusM)));
}

function applyPropertyOverlay(
  map: MapLibreMap,
  payload: PropertyOverlayPayload,
  mode: PropertyOverlayMode,
  onApplied?: () => void,
) {
  if (!map.isStyleLoaded()) {
    window.setTimeout(() => applyPropertyOverlay(map, payload, mode, onApplied), 120);
    return;
  }
  ensurePropertyOverlaySource(map);
  const source = map.getSource(OVERLAY_SOURCE_ID);
  if (source && "setData" in source) {
    (source as { setData: (data: unknown) => void }).setData(payload);
  }
  ensurePropertyOverlayLayers(map);
  NETWORK_OVERLAY_LAYER_IDS.forEach((layerId) => {
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(
        layerId,
        "visibility",
        mode === "mobile_network" ? "visible" : "none",
      );
    }
  });
  FOOTFALL_OVERLAY_LAYER_IDS.forEach((layerId) => {
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(
        layerId,
        "visibility",
        mode === "footfall" ? "visible" : "none",
      );
    }
  });
  onApplied?.();
}

function clearPropertyOverlay(map: MapLibreMap | null) {
  if (!map || !map.isStyleLoaded()) {
    return;
  }
  NETWORK_OVERLAY_LAYER_IDS.forEach((layerId) => {
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", "none");
    }
  });
  FOOTFALL_OVERLAY_LAYER_IDS.forEach((layerId) => {
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", "none");
    }
  });
  const source = map.getSource(OVERLAY_SOURCE_ID);
  if (source && "setData" in source) {
    (source as { setData: (data: unknown) => void }).setData({
      type: "FeatureCollection",
      features: [],
    });
  }
}

function overlayInteractiveLayerIds(
  map: MapLibreMap,
  mode: PropertyOverlayMode,
): string[] {
  if (mode === "mobile_network") {
    return NETWORK_OVERLAY_LAYER_IDS.filter((layerId) => map.getLayer(layerId));
  }
  if (mode === "footfall") {
    return FOOTFALL_OVERLAY_LAYER_IDS.filter((layerId) => map.getLayer(layerId));
  }
  return [];
}

function overlayFeatureAtPoint(
  map: MapLibreMap,
  payload: PropertyOverlayPayload | null,
  mode: PropertyOverlayMode,
  point: MapPointerPosition,
): PropertyOverlayFeature | null {
  if (!payload || payload.layer !== mode) {
    return null;
  }
  const centerFeature = payload.features.find((feature) => {
    if (feature.geometry.type !== "Point") {
      return false;
    }
    const coordinates = feature.geometry.coordinates;
    if (!isLngLatPair(coordinates)) {
      return false;
    }
    const projected = map.project(coordinates);
    return Math.hypot(projected.x - point.x, projected.y - point.y) <= 14;
  });
  if (centerFeature) {
    return centerFeature;
  }
  const lngLat = map.unproject([point.x, point.y]);
  const geographicHit = payload.features.find((feature) => {
    if (feature.geometry.type !== "Polygon") {
      return false;
    }
    const coordinates = feature.geometry.coordinates;
    if (!isPolygonCoordinates(coordinates)) {
      return false;
    }
    return polygonContainsLngLat(coordinates, [lngLat.lng, lngLat.lat]);
  });
  if (geographicHit) {
    return geographicHit;
  }
  return payload.features.find((feature) => {
    if (feature.geometry.type !== "Polygon") {
      return false;
    }
    const coordinates = feature.geometry.coordinates;
    if (!isPolygonCoordinates(coordinates)) {
      return false;
    }
    return projectedPolygonContainsPoint(map, coordinates, point);
  }) || null;
}

function isLngLatPair(value: number[] | number[][][]): value is [number, number] {
  return (
    Array.isArray(value)
    && value.length >= 2
    && typeof value[0] === "number"
    && typeof value[1] === "number"
  );
}

function isPolygonCoordinates(value: number[] | number[][][]): value is number[][][] {
  return (
    Array.isArray(value)
    && Array.isArray(value[0])
    && Array.isArray((value as number[][][])[0][0])
  );
}

function polygonContainsLngLat(polygon: number[][][], point: [number, number]): boolean {
  const [outerRing, ...holes] = polygon;
  if (!outerRing || !ringContainsPoint(outerRing, point)) {
    return false;
  }
  return !holes.some((ring) => ringContainsPoint(ring, point));
}

function projectedPolygonContainsPoint(
  map: MapLibreMap,
  polygon: number[][][],
  point: MapPointerPosition,
): boolean {
  const [outerRing, ...holes] = polygon.map((ring) =>
    ring.map(([lng, lat]) => {
      const projected = map.project([lng, lat]);
      return [projected.x, projected.y];
    }),
  );
  const screenPoint: [number, number] = [point.x, point.y];
  if (!outerRing || !ringContainsPoint(outerRing, screenPoint)) {
    return false;
  }
  return !holes.some((ring) => ringContainsPoint(ring, screenPoint));
}

function ringContainsPoint(ring: number[][], point: [number, number]): boolean {
  let inside = false;
  const [lng, lat] = point;
  for (let index = 0, previousIndex = ring.length - 1; index < ring.length; previousIndex = index++) {
    const current = ring[index];
    const previous = ring[previousIndex];
    if (!current || !previous) {
      continue;
    }
    const [currentLng, currentLat] = current;
    const [previousLng, previousLat] = previous;
    const intersects = (
      (currentLat > lat) !== (previousLat > lat)
      && lng < ((previousLng - currentLng) * (lat - currentLat)) / (previousLat - currentLat || Number.EPSILON) + currentLng
    );
    if (intersects) {
      inside = !inside;
    }
  }
  return inside;
}

function ensurePropertyOverlaySource(map: MapLibreMap) {
  if (map.getSource(OVERLAY_SOURCE_ID)) {
    return;
  }
  map.addSource(OVERLAY_SOURCE_ID, {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: [],
    },
  });
}

function ensurePropertyOverlayLayers(map: MapLibreMap) {
  if (!map.getLayer(NETWORK_LAYER_ID)) {
    map.addLayer({
      id: NETWORK_LAYER_ID,
      type: "fill",
      source: OVERLAY_SOURCE_ID,
      filter: ["all", ["==", ["geometry-type"], "Polygon"], ["==", ["get", "feature_kind"], "tile"]],
      layout: { visibility: "none" },
      paint: {
        "fill-color": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          "rgba(255, 74, 96, 0.96)",
          ["==", ["get", "performance_class"], "moderate"],
          "rgba(255, 190, 82, 0.86)",
          ["==", ["get", "performance_class"], "good"],
          "rgba(76, 231, 201, 0.76)",
          [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "score"], 0],
            0,
            "rgba(255, 74, 96, 0.94)",
            0.45,
            "rgba(255, 190, 82, 0.82)",
            0.72,
            "rgba(76, 231, 201, 0.72)",
            1,
            "rgba(238, 252, 249, 0.86)",
          ],
        ],
        "fill-opacity": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          0.84,
          ["==", ["get", "performance_class"], "moderate"],
          0.72,
          ["*",
            ["coalesce", ["get", "opacity"], 0.68],
            0.66,
          ],
        ],
        "fill-outline-color": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          "rgba(255, 245, 238, 0.92)",
          "rgba(238, 252, 249, 0.7)",
        ],
      },
    } as any);
  }
  if (!map.getLayer(NETWORK_OUTLINE_LAYER_ID)) {
    map.addLayer({
      id: NETWORK_OUTLINE_LAYER_ID,
      type: "line",
      source: OVERLAY_SOURCE_ID,
      filter: ["all", ["==", ["geometry-type"], "Polygon"], ["==", ["get", "feature_kind"], "tile"]],
      layout: { visibility: "none" },
      paint: {
        "line-color": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          "rgba(255, 98, 116, 0.98)",
          ["==", ["get", "performance_class"], "moderate"],
          "rgba(255, 215, 118, 0.84)",
          ["==", ["get", "performance_class"], "good"],
          "rgba(123, 255, 224, 0.72)",
          "rgba(238, 252, 249, 0.72)",
        ],
        "line-opacity": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          0.98,
          0.72,
        ],
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          13,
          0.7,
          16,
          1.25,
          18,
          2.15,
        ],
        "line-blur": 0.12,
      },
    } as any);
  }
  if (!map.getLayer(NETWORK_ALERT_HALO_LAYER_ID)) {
    map.addLayer({
      id: NETWORK_ALERT_HALO_LAYER_ID,
      type: "circle",
      source: OVERLAY_SOURCE_ID,
      filter: [
        "all",
        ["==", ["geometry-type"], "Point"],
        ["==", ["get", "feature_kind"], "tile_center"],
        ["==", ["get", "performance_class"], "poor"],
      ],
      layout: { visibility: "none" },
      paint: {
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          13,
          9,
          16,
          15,
          18,
          22,
        ],
        "circle-color": "rgba(255, 74, 96, 0.88)",
        "circle-opacity": 0.34,
        "circle-blur": 0.72,
      },
    } as any);
  }
  if (!map.getLayer(NETWORK_CENTER_LAYER_ID)) {
    map.addLayer({
      id: NETWORK_CENTER_LAYER_ID,
      type: "circle",
      source: OVERLAY_SOURCE_ID,
      filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "feature_kind"], "tile_center"]],
      layout: { visibility: "none" },
      paint: {
        "circle-radius": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          [
            "interpolate",
            ["linear"],
            ["zoom"],
            13,
            4.4,
            16,
            6.8,
            18,
            8.8,
          ],
          [
            "interpolate",
            ["linear"],
            ["zoom"],
            13,
            3.2,
            16,
            5.2,
            18,
            7.0,
          ],
        ],
        "circle-color": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          "rgba(255, 78, 102, 0.98)",
          ["==", ["get", "performance_class"], "moderate"],
          "rgba(255, 211, 110, 0.96)",
          ["==", ["get", "performance_class"], "good"],
          "rgba(98, 242, 220, 0.96)",
          "rgba(238, 252, 249, 0.98)",
        ],
        "circle-opacity": 0.96,
        "circle-stroke-color": [
          "case",
          ["==", ["get", "performance_class"], "poor"],
          "rgba(255, 250, 244, 0.96)",
          "rgba(4, 18, 19, 0.86)",
        ],
        "circle-stroke-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          13,
          1.2,
          18,
          2.4,
        ],
        "circle-stroke-opacity": 0.92,
        "circle-blur": 0.12,
      },
    } as any);
  }
  if (!map.getLayer(FOOTFALL_CELL_LAYER_ID)) {
    map.addLayer({
      id: FOOTFALL_CELL_LAYER_ID,
      type: "fill",
      source: OVERLAY_SOURCE_ID,
      filter: ["all", ["==", ["geometry-type"], "Polygon"], ["==", ["get", "feature_kind"], "footfall_cell"]],
      layout: { visibility: "none" },
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["coalesce", ["get", "score"], 0],
          0,
          "rgba(64, 183, 255, 0.24)",
          0.35,
          "rgba(98, 242, 220, 0.38)",
          0.68,
          "rgba(255, 211, 110, 0.5)",
          1,
          "rgba(238, 252, 249, 0.62)",
        ],
        "fill-opacity": [
          "interpolate",
          ["linear"],
          ["coalesce", ["get", "score"], 0],
          0,
          0.22,
          1,
          0.58,
        ],
        "fill-outline-color": "rgba(238, 252, 249, 0.54)",
      },
    } as any);
  }
  if (!map.getLayer(FOOTFALL_LAYER_ID)) {
    map.addLayer({
      id: FOOTFALL_LAYER_ID,
      type: "heatmap",
      source: OVERLAY_SOURCE_ID,
      filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "feature_kind"], "footfall_center"]],
      layout: { visibility: "none" },
      paint: {
        "heatmap-weight": ["interpolate", ["linear"], ["coalesce", ["get", "score"], 0], 0, 0, 1, 1],
        "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 13, 1.0, 18, 2.8],
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 13, 18, 16, 42, 18, 68],
        "heatmap-opacity": 0.84,
        "heatmap-color": [
          "interpolate",
          ["linear"],
          ["heatmap-density"],
          0,
          "rgba(4, 12, 15, 0)",
          0.22,
          "rgba(64, 183, 255, 0.32)",
          0.48,
          "rgba(98, 242, 220, 0.52)",
          0.72,
          "rgba(255, 211, 110, 0.68)",
          1,
          "rgba(238, 252, 249, 0.86)",
        ],
      },
    } as any);
  }
  if (!map.getLayer(FOOTFALL_CENTER_LAYER_ID)) {
    map.addLayer({
      id: FOOTFALL_CENTER_LAYER_ID,
      type: "circle",
      source: OVERLAY_SOURCE_ID,
      filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "feature_kind"], "footfall_center"]],
      layout: { visibility: "none" },
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4, 16, 6.5, 18, 9],
        "circle-color": "rgba(238, 252, 249, 0.96)",
        "circle-opacity": 0.96,
        "circle-stroke-color": "rgba(98, 242, 220, 0.84)",
        "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 13, 1.2, 18, 2.4],
        "circle-blur": 0.08,
      },
    } as any);
  }
}

function propertyOverlayStatusLabel(status: PropertyOverlayStatus): string {
  if (status === "ready") {
    return "Layer ready";
  }
  if (status === "empty") {
    return "No data";
  }
  if (status === "error") {
    return "Layer unavailable";
  }
  return "Layer";
}

function propertyOverlayStatusMessage(
  mode: PropertyOverlayMode,
  status: PropertyOverlayStatus,
  message: string,
  footfallProviderConfigured: boolean,
): string {
  if (mode === "mobile_network" && status === "ready") {
    return [
      "Hover tiles or center dots for download, upload, latency, tests and devices.",
      message,
    ].filter(Boolean).join(" ");
  }
  if (mode === "footfall" && status === "ready") {
    return [
      "Hover heat cells or white dots for observed public footfall values.",
      message,
    ].filter(Boolean).join(" ");
  }
  return message || propertyOverlayEmptyMessage(mode, footfallProviderConfigured);
}

function propertyOverlayEmptyMessage(
  mode: PropertyOverlayMode,
  footfallProviderConfigured: boolean,
): string {
  if (mode === "footfall" && !footfallProviderConfigured) {
    return "No public or configured footfall provider is available in this environment.";
  }
  if (mode === "footfall") {
    return "No public footfall observation is available within 5 km of this property.";
  }
  if (mode === "mobile_network") {
    return "No Ookla mobile tile is available within 5 km of this property.";
  }
  return "";
}

function networkOverlayHoverInfo(
  properties: Record<string, unknown>,
): MapHoverMarker {
  const download = formatMetricNumber(properties.download_mbps);
  const upload = formatMetricNumber(properties.upload_mbps);
  const latency = formatMetricNumber(
    properties.loaded_latency_down_ms ?? properties.latency_ms,
    0,
  );
  const tests = formatNumber(Number(properties.tests ?? 0));
  const devices = formatNumber(Number(properties.devices ?? 0));
  const classLabel = String(properties.performance_class || "Unknown");
  const confidence = String(properties.confidence || "unknown");
  const distanceM = formatMetricNumber(properties.distance_to_property_m, 0);
  const isCenter = properties.feature_kind === "tile_center";
  return {
    title: `${isCenter ? "Tile center · " : ""}${classLabel[0]?.toUpperCase() || ""}${classLabel.slice(1)} mobile experience`,
    detail: `${download} Mbps down · ${upload} Mbps up · ${latency} ms · ${tests} tests / ${devices} devices · ${distanceM} m · ${confidence}`,
    variant: "network",
  };
}

function footfallOverlayHoverInfo(
  properties: Record<string, unknown>,
): MapHoverMarker {
  const metricLabel = String(properties.metric_label || "Visits");
  const metricValue = formatNumber(Number(properties.metric_value ?? 0));
  const period = properties.period ? ` · ${String(properties.period)}` : "";
  const source = properties.source_name ? `${String(properties.source_name)} · ` : "";
  const distance = properties.distance_to_property_m !== null && properties.distance_to_property_m !== undefined
    ? ` · ${formatMetricNumber(properties.distance_to_property_m, 0)} m`
    : "";
  return {
    title: properties.feature_kind === "footfall_center" ? "Footfall observation center" : "Footfall observation",
    detail: `${source}${metricValue} ${metricLabel}${period}${distance}`,
    variant: "footfall",
  };
}

function formatMetricNumber(value: unknown, maximumFractionDigits = 1): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return "n/a";
  }
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits,
  }).format(numeric);
}

function sceneLabel(scene: string): string {
  return scene
    .split("_")
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "0";
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}
