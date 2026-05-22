import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Globe, { type GlobeMethods } from "react-globe.gl";
import { feature as topoFeature } from "topojson-client";
import countries110m from "world-atlas/countries-110m.json";
import {
  ArrowLeft,
  Bot,
  ChevronDown,
  ExternalLink,
  FileSpreadsheet,
  ImageOff,
  LayoutGrid,
  List,
  MapPin,
  Presentation,
  Search,
  X,
} from "lucide-react";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import type { GeoVisualMode, SatelliteBounds, SatelliteCamera, SatelliteMarker } from "./SatelliteNavigator";

import "./styles.css";

const SatelliteNavigator = React.lazy(() => import("./SatelliteNavigator"));

type HeroImage = {
  url: string;
  alt_text: string;
  source_name: string;
  source_url: string;
  source_date?: string | null;
  license?: string | null;
};

type Entity = {
  property_id: string;
  country: string;
  city: string;
  property_name: string;
  scene_type: string;
  latitude: number;
  longitude: number;
  geocode_precision: string;
  map_source?: string | null;
  map_source_date?: string | null;
  google_maps_link?: string | null;
  coordinate_status: string;
  hero_image?: HeroImage | null;
};

type EvidenceItem = {
  field_group: string;
  field_value: string;
  indicator_name?: string | null;
  source_name: string;
  source_url: string;
  source_date?: string | null;
  source_tier: string;
  evidence_type: string;
  cross_check_status?: string | null;
};

type InferenceRecord = {
  inferred_field: string;
  inferred_value: string;
  inference_basis: string;
  inference_chain: string;
  inference_confidence: string;
};

type ReviewItem = {
  reason: string;
  next_action: string;
  status: string;
};

type RagCitation = {
  chunk_id: string;
  source_url?: string | null;
  source_name?: string | null;
  source_tier?: string | null;
  source_date?: string | null;
  fetched_at?: string | null;
  raw_evidence_id?: string | null;
  property_id?: string | null;
  field_group?: string | null;
  excerpt: string;
};

type RagPriorityRecommendation = {
  property_id?: string | null;
  property_name: string;
  country?: string | null;
  scene_type?: string | null;
  priority_band: string;
  evidence_status?: string | null;
  action_class?: string | null;
  recommended_solution?: string | null;
  rationale: string;
  review_next_actions: string[];
};

type RagQueryResponse = {
  answer: string;
  citations: RagCitation[];
  priority_recommendations: RagPriorityRecommendation[];
  review_actions: string[];
};

type SitePacket = {
  entity: Entity;
  scene: {
    annual_visits_est?: number | null;
    proxy_level?: string | null;
    area_metric_name?: string | null;
    proxy_basis?: string | null;
  };
  evidence: EvidenceItem[];
  build_status: {
    indoor_system_presence: string;
    indoor_system_type: string;
    indoor_rat: string;
    build_evidence_status: string;
  };
  demand?: {
    busy_hour_traffic_gb?: number | null;
    busy_hour_bandwidth_mbps?: number | null;
    busy_hour_users?: number | null;
  } | null;
  inference: InferenceRecord[];
  conclusion: {
    evidence_status: string;
    value_class: string;
    action_class: string;
    recommended_solution: string;
    reason_to_recommend: string;
    next_action: string;
  };
  review_queue: ReviewItem[];
};

type MapFeature = {
  type: "Feature";
  geometry: {
    type: "Point";
    coordinates: [number, number];
  };
  properties: {
    property_id: string;
    property_name: string;
    country: string;
    city: string;
    scene_type: string;
    evidence_status: string;
    value_class: string;
    action_class: string;
    recommended_solution: string;
    main_metric_text: string;
    annual_visits_est?: number | null;
    busy_hour_traffic_gb?: number | null;
    review_count: number;
    source_count: number;
    indoor_system_presence: string;
    indoor_rat: string;
    proxy_level: string;
    google_maps_link?: string | null;
    geocode_precision: string;
    map_source?: string | null;
    coordinate_status: string;
    hero_image_url?: string | null;
    hero_image_alt?: string | null;
    hero_image_source_name?: string | null;
  };
};

type CountryMarker = {
  kind: "country";
  key: string;
  country: string;
  label: string;
  lat: number;
  lng: number;
  positionSource: CountryPositionSource;
  candidateCount: number;
  mapReadyCount: number;
  mappedCityCount: number;
  reviewCount: number;
  sourceCount: number;
  scenes: Record<string, number>;
  propertyIds: string[];
};

type CountryPositionSource = "property_average" | "polygon_centroid" | "display_anchor" | "default_fallback";
type CityPositionSource = "map_ready_average" | "city_geocode" | "property_average";

type CitySummary = {
  country: string;
  city: string;
  candidate_count: number;
  map_point_count: number;
  review_count: number;
  source_count: number;
  scenes: Record<string, number>;
  property_ids: string[];
  lat: number;
  lng: number;
  position_source: CityPositionSource;
};

type CityMarker = {
  kind: "city";
  key: string;
  country: string;
  city: string;
  label: string;
  lat: number;
  lng: number;
  positionSource: CityPositionSource;
  candidateCount: number;
  mapReadyCount: number;
  reviewCount: number;
  sourceCount: number;
  scenes: Record<string, number>;
  propertyIds: string[];
};

type CityClusterMarker = {
  kind: "city_cluster";
  key: string;
  country: string;
  label: string;
  lat: number;
  lng: number;
  cityCount: number;
  candidateCount: number;
  mapReadyCount: number;
  reviewCount: number;
  sourceCount: number;
  scenes: Record<string, number>;
  propertyIds: string[];
  cityKeys: string[];
};

type GlobeMarker = CountryMarker | CityMarker;
type DisplayGlobeMarker = GlobeMarker | CityClusterMarker;

type GeocodeResult = {
  latitude: number;
  longitude: number;
  geocode_precision: string;
  map_source: string;
  city?: string | null;
  country?: string | null;
  display_name?: string | null;
  boundingbox?: number[] | null;
};

type CityCentroidCacheValue = GeocodeResult | "missing" | "loading";

type ScreenGlobeMarker = GlobeMarker & {
  x: number;
  y: number;
  visible: boolean;
  spiderChild?: boolean;
};

type ScreenCityClusterMarker = CityClusterMarker & {
  x: number;
  y: number;
  visible: boolean;
  spiderChild?: boolean;
};

type ScreenDisplayGlobeMarker = ScreenGlobeMarker | ScreenCityClusterMarker;

type GlobeProjectionMethods = {
  pointOfView: () => { lat: number; lng: number; altitude: number };
  getScreenCoords: (lat: number, lng: number, altitude?: number) => { x: number; y: number };
};

type PropertiesResponse = {
  candidate_count: number;
  display_count: number;
  packets: SitePacket[];
};

type CountrySummary = {
  country: string;
  candidate_count: number;
  map_point_count: number;
  coordinate_review_count: number;
  review_count: number;
  source_count: number;
  scenes: Record<string, number>;
};

type RegionSummary = {
  region: string;
  country_count: number;
  candidate_count: number;
  map_point_count: number;
  review_count: number;
  source_count: number;
  scenes: Record<string, number>;
};

type DiscoveryStatus = {
  task_backlog: Record<string, number>;
  pending_evidence_count: number;
};

type CountryFeature = Feature<Geometry, { name?: string }>;

type Toast = {
  id: number;
  message: string;
  tone: "info" | "success" | "error";
};

type RuntimeConfig = {
  mode: string;
  features: {
    exports: boolean;
    rag: boolean;
    connectors: boolean;
    geocode: boolean;
  };
  map: {
    satelliteTileTemplate: string;
    satelliteTileSize: number;
    satelliteAttribution: string;
  };
};

type ViewMode = "overview" | "country" | "city" | "property";
type WorkspaceListMode = "card" | "dense";

type EvidenceGapSummary = {
  missingPrimaryMetricCount: number;
  missingImageCount: number;
  lowEvidenceCount: number;
  coordinateReviewCount: number;
  reviewQueueCount: number;
};

const DEFAULT_RUNTIME_CONFIG: RuntimeConfig = {
  mode: "local",
  features: {
    exports: true,
    rag: true,
    connectors: true,
    geocode: true,
  },
  map: {
    satelliteTileTemplate: "/map/satellite-tiles/{z}/{y}/{x}",
    satelliteTileSize: 512,
    satelliteAttribution: "Source: VersaTiles Satellite",
  },
};

const SCENE_LABELS: Record<string, string> = {
  airport_terminal: "Airport",
  convention_center: "Convention",
  stadium: "Stadium",
  luxury_hotel_mice: "Hotel / MICE",
  mall_mixed_use: "Mall / Mixed-use",
  office_government: "Office / Government",
  hospital: "Hospital",
  university: "University",
  transport_hub: "Transport hub",
  cruise_port: "Cruise port",
};

type ScenePrimaryMetricFilter = {
  label: string;
  indicators: string[];
};

const SCENE_PRIMARY_METRIC_FILTERS: Record<string, ScenePrimaryMetricFilter> = {
  airport_terminal: {
    label: "Passengers",
    indicators: [
      "annual_passenger_throughput",
      "annual_passengers",
      "annual_visits",
      "passenger_throughput",
      "passengers",
      "passenger_count",
      "passenger_movement",
      "passenger_movements",
      "terminal_capacity",
      "passenger_capacity",
    ],
  },
  convention_center: {
    label: "Area / capacity",
    indicators: [
      "exhibition_area",
      "meeting_area",
      "meeting_ballroom_area",
      "ballroom_capacity",
      "peak_event_capacity",
      "plenary_capacity",
      "annual_events",
    ],
  },
  stadium: {
    label: "Seats",
    indicators: ["seat_count", "seats", "capacity", "peak_event_capacity"],
  },
  luxury_hotel_mice: {
    label: "Rooms",
    indicators: ["keys", "room_count", "rooms", "guest_rooms"],
  },
  mall_mixed_use: {
    label: "GLA / footfall",
    indicators: [
      "gla",
      "nla",
      "retail_gfa",
      "mixed_use_area",
      "annual_footfall",
      "annual_visits",
      "footfall",
    ],
  },
  office_government: {
    label: "Area / floors",
    indicators: [
      "office_nla",
      "office_gfa",
      "floor_count",
      "tower_height",
      "site_area",
    ],
  },
  hospital: {
    label: "Beds",
    indicators: ["beds", "bed_count", "outpatient_volume"],
  },
  university: {
    label: "Enrollment",
    indicators: ["enrollment", "campus_population"],
  },
  transport_hub: {
    label: "Ridership",
    indicators: [
      "daily_ridership",
      "interchange_volume",
      "passenger_throughput",
      "annual_passenger_throughput",
    ],
  },
  cruise_port: {
    label: "Passengers",
    indicators: ["passenger_throughput", "annual_passenger_throughput"],
  },
};

type TargetRegion = "Latin America" | "Asia Pacific" | "Middle East & Central Asia" | "Africa";

const TARGET_REGION_ORDER: TargetRegion[] = [
  "Latin America",
  "Asia Pacific",
  "Middle East & Central Asia",
  "Africa",
];

const REGION_COUNTRIES: Record<TargetRegion, string[]> = {
  "Latin America": [
    "Antigua and Barbuda",
    "Argentina",
    "Bahamas",
    "Barbados",
    "Belize",
    "Bolivia",
    "Brazil",
    "Chile",
    "Colombia",
    "Costa Rica",
    "Cuba",
    "Dominica",
    "Dominican Republic",
    "Ecuador",
    "El Salvador",
    "Grenada",
    "Guatemala",
    "Guyana",
    "Haiti",
    "Honduras",
    "Jamaica",
    "Mexico",
    "Nicaragua",
    "Panama",
    "Paraguay",
    "Peru",
    "Saint Kitts and Nevis",
    "Saint Lucia",
    "Saint Vincent and the Grenadines",
    "Suriname",
    "Trinidad and Tobago",
    "Uruguay",
    "Venezuela",
  ],
  "Asia Pacific": [
    "Australia",
    "Bangladesh",
    "Bhutan",
    "Brunei",
    "Cambodia",
    "China",
    "Fiji",
    "India",
    "Indonesia",
    "Japan",
    "Kiribati",
    "Laos",
    "Malaysia",
    "Maldives",
    "Marshall Islands",
    "Micronesia",
    "Mongolia",
    "Myanmar",
    "Nauru",
    "Nepal",
    "New Zealand",
    "Pakistan",
    "Palau",
    "Papua New Guinea",
    "Philippines",
    "Samoa",
    "Singapore",
    "Solomon Islands",
    "South Korea",
    "Sri Lanka",
    "Taiwan",
    "Thailand",
    "Timor-Leste",
    "Tonga",
    "Tuvalu",
    "Vanuatu",
    "Vietnam",
  ],
  "Middle East & Central Asia": [
    "Afghanistan",
    "Armenia",
    "Azerbaijan",
    "Bahrain",
    "Georgia",
    "Iran",
    "Iraq",
    "Israel",
    "Jordan",
    "Kazakhstan",
    "Kuwait",
    "Kyrgyzstan",
    "Lebanon",
    "Oman",
    "Palestine",
    "Qatar",
    "Saudi Arabia",
    "Syria",
    "Tajikistan",
    "Turkey",
    "Turkmenistan",
    "United Arab Emirates",
    "Uzbekistan",
    "Yemen",
  ],
  Africa: [
    "Algeria",
    "Angola",
    "Benin",
    "Botswana",
    "Burkina Faso",
    "Burundi",
    "Cabo Verde",
    "Cameroon",
    "Central African Republic",
    "Chad",
    "Comoros",
    "Congo",
    "Cote d'Ivoire",
    "Democratic Republic of the Congo",
    "Djibouti",
    "Egypt",
    "Equatorial Guinea",
    "Eritrea",
    "Eswatini",
    "Ethiopia",
    "Gabon",
    "Gambia",
    "Ghana",
    "Guinea",
    "Guinea-Bissau",
    "Kenya",
    "Lesotho",
    "Liberia",
    "Libya",
    "Madagascar",
    "Malawi",
    "Mali",
    "Mauritania",
    "Mauritius",
    "Morocco",
    "Mozambique",
    "Namibia",
    "Niger",
    "Nigeria",
    "Rwanda",
    "Sao Tome and Principe",
    "Senegal",
    "Seychelles",
    "Sierra Leone",
    "Somalia",
    "South Africa",
    "South Sudan",
    "Sudan",
    "Tanzania",
    "Togo",
    "Tunisia",
    "Uganda",
    "Zambia",
    "Zimbabwe",
  ],
};
const REGION_BY_COUNTRY = new Map(
  Object.entries(REGION_COUNTRIES).flatMap(([region, countries]) =>
    countries.map((country) => [normalizeCountryName(country), region] as const),
  ),
);

const COUNTRY_DISPLAY_ANCHORS: Record<string, { lat: number; lng: number }> = {
  [normalizeCountryName("Maldives")]: { lat: 3.2028, lng: 73.2207 },
  [normalizeCountryName("Seychelles")]: { lat: -4.6796, lng: 55.492 },
  [normalizeCountryName("Comoros")]: { lat: -11.6455, lng: 43.3333 },
  [normalizeCountryName("Mauritius")]: { lat: -20.3484, lng: 57.5522 },
  [normalizeCountryName("Bahrain")]: { lat: 26.0667, lng: 50.5577 },
  [normalizeCountryName("Singapore")]: { lat: 1.3521, lng: 103.8198 },
};
const TINY_COUNTRY_POLYGON_SPAN_DEGREES = 1.2;

const ASSET_BASE_URL = import.meta.env.BASE_URL;
const EARTH_IMAGE = `${ASSET_BASE_URL}earth-blue-marble.jpg`;
const EARTH_BUMP = `${ASSET_BASE_URL}earth-topology.png`;
const OVERVIEW_ALTITUDE = 1.85;
const COUNTRY_ALTITUDE = 1.35;
const PROPERTY_ALTITUDE = 0.55;
const OVERVIEW_ROTATE_SPEED = 0.24;
const CITY_CLUSTER_RADIUS_PX = 84;
const SPIDER_BASE_RADIUS_PX = 78;
const SPIDER_RING_GAP_PX = 44;
const SPIDER_FIRST_RING_CAPACITY = 8;
const SPIDER_EDGE_PADDING_PX = 56;

type GlobeMode = "overview" | "focused";
type GlobeControlState = {
  autoRotate: boolean;
  autoRotateSpeed: number;
  enableDamping: boolean;
};

function initialPointOfViewOverride(): { lat: number; lng: number } | null {
  const value = new URLSearchParams(window.location.search).get("test_pov");
  if (!value) {
    return null;
  }
  const [lat, lng] = value.split(",").map((item) => Number(item.trim()));
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return null;
  }
  return { lat, lng };
}

function App() {
  const globeRef = useRef<GlobeMethods | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const loadSequenceRef = useRef(0);
  const countrySummaryCacheRef = useRef<CountrySummary[] | null>(null);
  const globeModeRef = useRef<GlobeMode>("overview");
  const pointOfViewOverrideRef = useRef<{ lat: number; lng: number } | null>(
    initialPointOfViewOverride(),
  );
  const [stageSize, setStageSize] = useState({ width: 900, height: 720 });
  const [globeReady, setGlobeReady] = useState(false);
  const [features, setFeatures] = useState<MapFeature[]>([]);
  const [packets, setPackets] = useState<SitePacket[]>([]);
  const [summaries, setSummaries] = useState<CountrySummary[]>([]);
  const [citySummaries, setCitySummaries] = useState<CitySummary[]>([]);
  const [selectedCountry, setSelectedCountry] = useState<string>("");
  const [selectedCityKey, setSelectedCityKey] = useState<string>("");
  const [hoveredCountry, setHoveredCountry] = useState<string>("");
  const [selectedPropertyId, setSelectedPropertyId] = useState<string>("");
  const [isCountryScopeOpen, setIsCountryScopeOpen] = useState(false);
  const [countryQuery, setCountryQuery] = useState("");
  const [isRagOpen, setIsRagOpen] = useState(false);
  const [ragQuestion, setRagQuestion] = useState("");
  const [ragResult, setRagResult] = useState<RagQueryResponse | null>(null);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragError, setRagError] = useState("");
  const [sceneFilter, setSceneFilter] = useState("");
  const [panelSceneFilter, setPanelSceneFilter] = useState("");
  const [evidenceFilter, setEvidenceFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [reviewOnly, setReviewOnly] = useState(false);
  const [listMode, setListMode] = useState<WorkspaceListMode>("card");
  const [expandedClusterKey, setExpandedClusterKey] = useState("");
  const [screenGlobeMarkers, setScreenGlobeMarkers] = useState<ScreenDisplayGlobeMarker[]>([]);
  const [cityCentroidCache, setCityCentroidCache] = useState<Map<string, CityCentroidCacheValue>>(
    () => new Map(),
  );
  const [detailTab, setDetailTab] = useState<"evidence" | "inference" | "review">("evidence");
  const [loading, setLoading] = useState(true);
  const [packetsLoading, setPacketsLoading] = useState(true);
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(DEFAULT_RUNTIME_CONFIG);

  const useMockGlobe = useMemo(() => new URLSearchParams(window.location.search).has("mock_globe"), []);
  const useMockCluster = useMemo(() => new URLSearchParams(window.location.search).has("mock_cluster"), []);
  const useMockSatellite = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.has("mock_satellite") || params.has("mock_globe");
  }, []);
  const enabledFeatures = runtimeConfig.features;
  const countryPolygons = useMemo(() => loadCountryPolygons(), []);
  const countryDisplayPositionCache = useMemo(
    () => buildCountryDisplayPositionCache(countryPolygons),
    [countryPolygons],
  );
  const countryNamesWithData = useMemo(
    () => new Set(summaries.map((summary) => summary.country)),
    [summaries],
  );
  const selectedSummary = useMemo(
    () => aggregateSummary(summaries, selectedCountry),
    [summaries, selectedCountry],
  );
  const regionSummaries = useMemo(
    () => aggregateRegionSummaries(summaries),
    [summaries],
  );
  const countryMarkers = useMemo(
    () => aggregateCountryMarkers(summaries, features, countryDisplayPositionCache),
    [countryDisplayPositionCache, features, summaries],
  );
  const metricFilteredPackets = packets;
  const cityMarkers = useMemo(
    () => {
      const summaryMarkers = aggregateCitySummaryMarkers(citySummaries);
      if (!selectedCountry) {
        return summaryMarkers;
      }
      const packetMarkers = aggregateCityMarkers(metricFilteredPackets, features, cityCentroidCache);
      return packetMarkers.length > 0 ? packetMarkers : summaryMarkers;
    },
    [cityCentroidCache, citySummaries, features, metricFilteredPackets, selectedCountry],
  );
  const selectedCountryCityCount = useMemo(
    () => selectedCountry
      ? cityMarkers.filter((marker) => marker.country === selectedCountry).length
      : 0,
    [cityMarkers, selectedCountry],
  );
  const visibleGlobeMarkers = useMemo<GlobeMarker[]>(
    () => selectedCountry
      ? cityMarkers.filter((marker) => marker.country === selectedCountry)
      : countryMarkers,
    [cityMarkers, countryMarkers, selectedCountry],
  );
  const mockScreenGlobeMarkers = useMemo<ScreenDisplayGlobeMarker[]>(() => {
    if (!useMockGlobe || !useMockCluster) {
      return [];
    }
    const projected = projectMockGlobeMarkers(
      visibleGlobeMarkers,
      selectedCityKey,
      stageSize,
      pointOfViewOverrideRef.current,
    );
    return clusterScreenGlobeMarkers(projected, {
      expandedClusterKey,
      selectedCityKey,
      selectedCountry,
      stageSize,
    });
  }, [
    expandedClusterKey,
    selectedCityKey,
    selectedCountry,
    stageSize,
    useMockCluster,
    useMockGlobe,
    visibleGlobeMarkers,
  ]);
  const selectedCityMarker = useMemo(
    () => cityMarkers.find((marker) => marker.key === selectedCityKey) ?? null,
    [cityMarkers, selectedCityKey],
  );
  const visiblePackets = useMemo(() => {
    if (!selectedCityKey) {
      return metricFilteredPackets;
    }
    return metricFilteredPackets.filter(
      (packet) => cityKey(packet.entity.country, packet.entity.city) === selectedCityKey,
    );
  }, [metricFilteredPackets, selectedCityKey]);
  const panelPackets = useMemo(
    () => filterAndSortPanelPackets(visiblePackets, panelSceneFilter),
    [panelSceneFilter, visiblePackets],
  );
  const panelSummary = useMemo(
    () => {
      if (selectedCityKey) {
        return summaryFromPackets(visiblePackets, selectedCityMarker);
      }
      return selectedCountry ? summaryFromPackets(metricFilteredPackets, null) : selectedSummary;
    },
    [
      metricFilteredPackets,
      selectedCityKey,
      selectedCityMarker,
      selectedCountry,
      selectedSummary,
      visiblePackets,
    ],
  );
  const selectedPacket = useMemo(
    () => metricFilteredPackets.find((packet) => packet.entity.property_id === selectedPropertyId) ?? null,
    [metricFilteredPackets, selectedPropertyId],
  );
  const viewMode = deriveViewMode(selectedCountry, selectedCityKey, selectedPropertyId);
  const gapSummary = useMemo(
    () => summarizeEvidenceGaps(panelPackets),
    [panelPackets],
  );
  const geoVisualMode = deriveGeoVisualMode(viewMode);
  const satelliteCamera = useMemo(
    () => satelliteCameraForScope({
      mode: geoVisualMode,
      selectedCountry,
      selectedCityMarker,
      selectedPacket,
      cityMarkers,
      visiblePackets,
      metricFilteredPackets,
      features,
      countryPolygons,
    }),
    [
      cityMarkers,
      countryPolygons,
      features,
      geoVisualMode,
      metricFilteredPackets,
      selectedCityMarker,
      selectedCountry,
      selectedPacket,
      visiblePackets,
    ],
  );
  const satelliteMarkers = useMemo(
    () => satelliteMarkersForScope({
      mode: geoVisualMode,
      selectedCountry,
      selectedCityKey,
      selectedPropertyId,
      selectedPacket,
      cityMarkers,
      visiblePackets,
      metricFilteredPackets,
    }),
    [
      cityMarkers,
      geoVisualMode,
      metricFilteredPackets,
      selectedCityKey,
      selectedCountry,
      selectedPacket,
      selectedPropertyId,
      visiblePackets,
    ],
  );
  const workspaceContext = useMemo(
    () => workspaceContextLabel({
      selectedCountry,
      selectedCityMarker,
      panelSceneFilter,
      sceneFilter,
      evidenceFilter,
      actionFilter,
      reviewOnly,
    }),
    [
      actionFilter,
      evidenceFilter,
      panelSceneFilter,
      reviewOnly,
      sceneFilter,
      selectedCityMarker,
      selectedCountry,
    ],
  );
  const globeMode: GlobeMode = viewMode === "overview" ? "overview" : "focused";
  globeModeRef.current = globeMode;
  const overviewProjectedMarkerCount = useMockGlobe
    ? useMockCluster
      ? mockScreenGlobeMarkers.length
      : visibleGlobeMarkers.length
    : screenGlobeMarkers.length;
  const overviewSummaryReady = !loading;
  const overviewRegionReady = overviewSummaryReady && (summaries.length === 0 || regionSummaries.length > 0);
  const overviewMarkersReady = overviewSummaryReady
    && (visibleGlobeMarkers.length === 0 || overviewProjectedMarkerCount > 0);
  const initialOverviewReady = viewMode !== "overview"
    || (globeReady && overviewSummaryReady && overviewRegionReady && overviewMarkersReady);
  const showInitialThinking = viewMode === "overview" && !initialOverviewReady;
  const aiThinkingStage = aiThinkingStageLabel({
    globeReady,
    overviewMarkersReady,
    overviewRegionReady,
    overviewSummaryReady,
  });

  const notify = useCallback((message: string, tone: Toast["tone"] = "info") => {
    const id = Date.now();
    setToast({ id, message, tone });
    window.setTimeout(() => {
      setToast((current) => (current?.id === id ? null : current));
    }, 2800);
  }, []);

  const applyGlobeMode = useCallback((mode: GlobeMode) => {
    globeModeRef.current = mode;
    configureGlobeControls(globeRef.current, mode);
  }, []);

  useEffect(() => {
    let active = true;
    void fetchJson<RuntimeConfig>("/runtime-config")
      .then((config) => {
        if (active) {
          setRuntimeConfig(normalizeRuntimeConfig(config));
        }
      })
      .catch(() => {
        if (active) {
          setRuntimeConfig(DEFAULT_RUNTIME_CONFIG);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (useMockGlobe) {
      setGlobeReady(true);
    }
  }, [useMockGlobe]);

  const askRag = useCallback(async () => {
    if (!enabledFeatures.rag) {
      return;
    }
    const question = ragQuestion.trim();
    if (!question || ragLoading) {
      return;
    }
    setRagLoading(true);
    setRagError("");
    try {
      const context = {
        property_id: selectedPacket?.entity.property_id,
        country: selectedPacket?.entity.country || selectedCountry || undefined,
      };
      await fetchJson("/rag/index", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(context),
      });
      const result = await fetchJson<RagQueryResponse>("/rag/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...context,
          scene_type: selectedPacket?.entity.scene_type || sceneFilter || undefined,
          question,
          top_k: 6,
        }),
      });
      setRagResult(result);
    } catch (error) {
      setRagError(errorMessage(error));
    } finally {
      setRagLoading(false);
    }
  }, [
    enabledFeatures.rag,
    ragLoading,
    ragQuestion,
    sceneFilter,
    selectedCountry,
    selectedPacket,
  ]);

  const loadData = useCallback(async () => {
    const sequence = ++loadSequenceRef.current;
    setLoading(true);
    setPacketsLoading(true);
    setFeatures([]);
    setPackets([]);
    setCitySummaries([]);
    const params = new URLSearchParams();
    addParam(params, "country", selectedCountry);
    addParam(params, "scene_type", sceneFilter);
    addParam(params, "evidence_status", evidenceFilter);
    addParam(params, "action_class", actionFilter);
    if (reviewOnly) {
      params.set("has_review_issue", "true");
    }
    const query = params.toString();
    try {
      const cachedCountrySummary = countrySummaryCacheRef.current;
      const [countrySummary, citySummary] = await Promise.all([
        cachedCountrySummary
          ? Promise.resolve(cachedCountrySummary)
          : fetchJson<CountrySummary[]>("/map/country-summary"),
        selectedCountry
          ? fetchJson<{ cities: CitySummary[] }>(withQuery("/map/city-summary", query))
          : Promise.resolve({ cities: [] }),
      ]);
      if (sequence !== loadSequenceRef.current) {
        return;
      }
      if (!countrySummaryCacheRef.current) {
        countrySummaryCacheRef.current = countrySummary || [];
      }
      setSummaries(countrySummaryCacheRef.current);
      setCitySummaries(citySummary.cities || []);
      setLoading(false);

      if (!selectedCountry) {
        setFeatures([]);
        setPackets([]);
        setCitySummaries([]);
        setSelectedPropertyId("");
        setPacketsLoading(false);
        return;
      }

      await fetchJson<PropertiesResponse>(withQuery("/properties", query))
        .then((properties) => {
          if (sequence !== loadSequenceRef.current) {
            return;
          }
          setPackets(properties.packets || []);
          setSelectedPropertyId((current) => {
            return properties.packets?.some((packet) => packet.entity.property_id === current)
              ? current
              : "";
          });
        })
        .finally(() => {
          if (sequence === loadSequenceRef.current) {
            setPacketsLoading(false);
          }
        });
    } catch (error) {
      if (sequence === loadSequenceRef.current) {
        notify(errorMessage(error), "error");
        setPacketsLoading(false);
      }
    } finally {
      if (sequence === loadSequenceRef.current) {
        setLoading(false);
      }
    }
  }, [
    actionFilter,
    evidenceFilter,
    notify,
    reviewOnly,
    sceneFilter,
    selectedCountry,
  ]);

  useEffect(() => {
    const element = stageRef.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) {
        setStageSize({
          width: Math.max(320, Math.floor(rect.width)),
          height: Math.max(360, Math.floor(rect.height)),
        });
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    applyGlobeMode(globeMode);
  }, [applyGlobeMode, globeMode]);

  useEffect(() => {
    configureGlobeControls(globeRef.current, globeMode);
    const interval = window.setInterval(
      () => configureGlobeControls(globeRef.current, globeMode),
      500,
    );
    return () => window.clearInterval(interval);
  }, [globeMode]);

  useEffect(() => {
    if (!selectedCityKey || loading) {
      return;
    }
    if (!cityMarkers.some((marker) => marker.key === selectedCityKey)) {
      setSelectedCityKey("");
    }
  }, [cityMarkers, loading, selectedCityKey]);

  useEffect(() => {
    if (!selectedPropertyId) {
      return;
    }
    if (!metricFilteredPackets.some((packet) => packet.entity.property_id === selectedPropertyId)) {
      setSelectedPropertyId("");
    }
  }, [metricFilteredPackets, selectedPropertyId]);

  useEffect(() => {
    setPanelSceneFilter("");
  }, [selectedCountry, selectedCityKey]);

  useEffect(() => {
    if (!panelSceneFilter) {
      return;
    }
    if (!visiblePackets.some((packet) => packet.entity.scene_type === panelSceneFilter)) {
      setPanelSceneFilter("");
    }
  }, [panelSceneFilter, visiblePackets]);

  useEffect(() => {
    setExpandedClusterKey("");
  }, [
    actionFilter,
    evidenceFilter,
    reviewOnly,
    sceneFilter,
    selectedCityKey,
    selectedCountry,
  ]);

  useEffect(() => {
    if (!selectedCountry || citySummaries.length > 0) {
      return;
    }
    const missingCities = citiesNeedingCentroids(
      metricFilteredPackets,
      features,
      selectedCountry,
      cityCentroidCache,
    );
    if (missingCities.length === 0) {
      return;
    }

    if (!enabledFeatures.geocode) {
      setCityCentroidCache((current) => {
        const next = new Map(current);
        missingCities.forEach((city) => next.set(city.key, "missing"));
        return next;
      });
      return;
    }

    setCityCentroidCache((current) => {
      const next = new Map(current);
      missingCities.forEach((city) => next.set(city.key, "loading"));
      return next;
    });

    missingCities.forEach((city) => {
      const query = `${city.city}, ${city.country}`;
      void fetchJson<GeocodeResult>(`/connectors/geocode?q=${encodeURIComponent(query)}`)
        .then((result) => {
          setCityCentroidCache((current) => {
            const next = new Map(current);
            next.set(city.key, isUsableGeocodeResult(result) ? result : "missing");
            return next;
          });
        })
        .catch(() => {
          setCityCentroidCache((current) => {
            const next = new Map(current);
            next.set(city.key, "missing");
            return next;
          });
        });
    });
  }, [
    cityCentroidCache,
    citySummaries.length,
    enabledFeatures.geocode,
    features,
    metricFilteredPackets,
    selectedCountry,
  ]);

  useEffect(() => {
    if (useMockGlobe) {
      setScreenGlobeMarkers([]);
      return;
    }

    let frame = 0;
    const updateMarkerProjection = () => {
      const globe = globeRef.current as unknown as GlobeProjectionMethods | null;
      configureGlobeControls(globeRef.current, globeModeRef.current);
      if (!globe || visibleGlobeMarkers.length === 0) {
        setScreenGlobeMarkers((current) => current.length > 0 ? [] : current);
        frame = window.requestAnimationFrame(updateMarkerProjection);
        return;
      }

      try {
        const projected = projectGlobeMarkers(
          globe,
          visibleGlobeMarkers,
          selectedCityKey,
          pointOfViewOverrideRef.current,
        );
        const displayMarkers = clusterScreenGlobeMarkers(projected, {
          expandedClusterKey,
          selectedCityKey,
          selectedCountry,
          stageSize,
        });
        setScreenGlobeMarkers((current) =>
          sameScreenGlobeMarkers(current, displayMarkers) ? current : displayMarkers,
        );
      } catch {
        setScreenGlobeMarkers((current) => current.length > 0 ? [] : current);
      }

      frame = window.requestAnimationFrame(updateMarkerProjection);
    };

    frame = window.requestAnimationFrame(updateMarkerProjection);
    return () => window.cancelAnimationFrame(frame);
  }, [
    expandedClusterKey,
    selectedCityKey,
    selectedCountry,
    stageSize,
    useMockGlobe,
    visibleGlobeMarkers,
  ]);

  useEffect(() => {
    if (viewMode === "overview" && !initialOverviewReady) {
      return;
    }
    const refreshDiscoveryStatus = async () => {
      try {
        setDiscoveryStatus(await fetchJson<DiscoveryStatus>("/discovery/status"));
      } catch {
        setDiscoveryStatus(null);
      }
    };
    void refreshDiscoveryStatus();
    const interval = window.setInterval(refreshDiscoveryStatus, 30000);
    return () => window.clearInterval(interval);
  }, [initialOverviewReady, viewMode]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsCountryScopeOpen(false);
        setIsRagOpen(false);
        setExpandedClusterKey("");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    const pointOfViewOverride = pointOfViewOverrideRef.current;
    if (pointOfViewOverride) {
      globeModeRef.current = "focused";
      globeRef.current?.pointOfView({
        lat: pointOfViewOverride.lat,
        lng: pointOfViewOverride.lng,
        altitude: OVERVIEW_ALTITUDE,
      }, 0);
      configureGlobeControls(globeRef.current, "focused");
      return;
    }
    const center = selectedCityMarker
      ? cityFocus(selectedCityMarker)
      : selectedCountry
      ? countryFocus(selectedCountry, features, countryPolygons)
      : propertyFocus(features);
    const mode: GlobeMode = selectedCityMarker || selectedCountry ? "focused" : "overview";
    globeRef.current?.pointOfView({
      lat: center.lat,
      lng: center.lng,
      altitude: selectedCityMarker ? PROPERTY_ALTITUDE : selectedCountry ? COUNTRY_ALTITUDE : OVERVIEW_ALTITUDE,
    }, 1200);
    applyGlobeMode(mode);
    const timer = window.setTimeout(() => applyGlobeMode(mode), 1250);
    return () => window.clearTimeout(timer);
  }, [applyGlobeMode, countryPolygons, features, selectedCityMarker, selectedCountry]);

  useEffect(() => {
    if (!selectedPropertyId) {
      return;
    }
    const target = features.find(
      (item) => item.properties.property_id === selectedPropertyId,
    );
    if (target) {
      const [lng, lat] = target.geometry.coordinates;
      globeRef.current?.pointOfView({ lat, lng, altitude: PROPERTY_ALTITUDE }, 900);
      applyGlobeMode("focused");
      const timer = window.setTimeout(() => applyGlobeMode("focused"), 950);
      return () => window.clearTimeout(timer);
    }
    const entity = selectedPacket?.entity;
    if (!entity || !Number.isFinite(entity.latitude) || !Number.isFinite(entity.longitude)) {
      return;
    }
    globeRef.current?.pointOfView({
      lat: entity.latitude,
      lng: entity.longitude,
      altitude: PROPERTY_ALTITUDE,
    }, 900);
    applyGlobeMode("focused");
    const timer = window.setTimeout(() => applyGlobeMode("focused"), 950);
    return () => window.clearTimeout(timer);
  }, [applyGlobeMode, features, selectedPacket, selectedPropertyId]);

  useLayoutEffect(() => {
    window.__isite2SelectCountry = (country: string) => {
      applyGlobeMode(country ? "focused" : "overview");
      setSelectedCountry(country);
      setSelectedCityKey("");
      setSelectedPropertyId("");
      setPanelSceneFilter("");
      setExpandedClusterKey("");
      setIsCountryScopeOpen(false);
      setCountryQuery("");
    };
    window.__isite2GlobeControlState = () => {
      const mode = stageRef.current?.dataset.globeMode === "focused" ? "focused" : "overview";
      configureGlobeControls(globeRef.current, mode);
      const controls = globeRef.current?.controls();
      return controls
        ? {
            autoRotate: mode === "overview",
            autoRotateSpeed: controls.autoRotateSpeed,
            enableDamping: controls.enableDamping,
          }
        : {
            autoRotate: mode === "overview",
            autoRotateSpeed: OVERVIEW_ROTATE_SPEED,
            enableDamping: true,
          };
    };
    window.__isite2SetPointOfView = (lat: number, lng: number, altitude = OVERVIEW_ALTITUDE) => {
      pointOfViewOverrideRef.current = { lat, lng };
      globeModeRef.current = "focused";
      configureGlobeControls(globeRef.current, "focused");
      globeRef.current?.pointOfView({ lat, lng, altitude }, 0);
    };
    return () => {
      delete window.__isite2SelectCountry;
      delete window.__isite2GlobeControlState;
      delete window.__isite2SetPointOfView;
    };
  }, [applyGlobeMode]);

  const handleCountrySelect = useCallback((country: string) => {
    applyGlobeMode(country ? "focused" : "overview");
    setSelectedCountry(country);
    setSelectedCityKey("");
    setSelectedPropertyId("");
    setPanelSceneFilter("");
    setExpandedClusterKey("");
    setIsCountryScopeOpen(false);
    setCountryQuery("");
  }, [applyGlobeMode]);

  const handleCitySelect = useCallback((marker: CityMarker) => {
    applyGlobeMode("focused");
    setSelectedCountry(marker.country);
    setSelectedCityKey(marker.key);
    setSelectedPropertyId("");
    setPanelSceneFilter("");
    setExpandedClusterKey("");
  }, [applyGlobeMode]);

  const handleGlobeMarkerSelect = useCallback((marker: DisplayGlobeMarker) => {
    if (marker.kind === "country") {
      handleCountrySelect(marker.country);
      return;
    }
    if (marker.kind === "city_cluster") {
      applyGlobeMode("focused");
      setExpandedClusterKey(marker.key);
      return;
    }
    handleCitySelect(marker);
  }, [applyGlobeMode, handleCitySelect, handleCountrySelect]);

  const handlePropertySelect = useCallback((propertyId: string) => {
    applyGlobeMode("focused");
    setSelectedPropertyId(propertyId);
  }, [applyGlobeMode]);

  const handleSatelliteMarkerSelect = useCallback((marker: SatelliteMarker) => {
    applyGlobeMode("focused");
    if (marker.kind === "city_cluster" || marker.kind === "property_cluster") {
      return;
    }
    if (marker.kind === "city") {
      const city = cityMarkers.find((item) => item.key === marker.id);
      if (city) {
        handleCitySelect(city);
      }
      return;
    }
    handlePropertySelect(marker.id);
  }, [applyGlobeMode, cityMarkers, handleCitySelect, handlePropertySelect]);

  const handleBackToList = useCallback(() => {
    applyGlobeMode(selectedCountry ? "focused" : "overview");
    setSelectedPropertyId("");
  }, [applyGlobeMode, selectedCountry]);

  const handleClearCity = useCallback(() => {
    applyGlobeMode(selectedCountry ? "focused" : "overview");
    setSelectedCityKey("");
    setSelectedPropertyId("");
    setPanelSceneFilter("");
    setExpandedClusterKey("");
  }, [applyGlobeMode, selectedCountry]);

  const handlePanelSceneSelect = useCallback((scene: string) => {
    setPanelSceneFilter((current) => current === scene ? "" : scene);
  }, []);

  const handleCountryClick = useCallback(
    (country: CountryFeature) => {
      const name = countryName(country);
      if (!name) {
        return;
      }
      handleCountrySelect(name);
    },
    [handleCountrySelect],
  );

  const handleExport = useCallback(
    async (type: "excel" | "ppt") => {
      if (!enabledFeatures.exports) {
        return;
      }
      const body = exportFilterPayload({
        selectedCountry,
        selectedCityMarker,
        sceneFilter,
        evidenceFilter,
        actionFilter,
        reviewOnly,
      });
      try {
        await fetchJson(`/outputs/${type}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        notify(`${type.toUpperCase()} queued`, "success");
      } catch (error) {
        notify(errorMessage(error), "error");
      }
    },
    [
      actionFilter,
      enabledFeatures.exports,
      evidenceFilter,
      notify,
      reviewOnly,
      sceneFilter,
      selectedCityMarker,
      selectedCountry,
    ],
  );

  return (
    <main
      className="app-shell"
      data-view-mode={viewMode}
    >
      <section
        className="globe-stage"
        ref={stageRef}
        aria-label={geoVisualMode === "globe" ? "3D opportunity globe" : "Satellite opportunity map"}
        data-geo-visual-mode={geoVisualMode}
        data-globe-mode={globeMode}
        data-auto-rotate={globeMode === "overview" ? "true" : "false"}
      >
        <header className="topbar">
          <div className="brand-lockup">
            <span className="eyebrow">iSite2</span>
            <h1>Opportunity Globe</h1>
          </div>
          <div className="toolbar" aria-label="Opportunity actions">
            {enabledFeatures.rag && (
              <button
                aria-label="Ask iSite2"
                type="button"
                onClick={() => setIsRagOpen(true)}
              >
                <Bot size={16} aria-hidden="true" />
                <span>Ask iSite2</span>
              </button>
            )}
            {enabledFeatures.exports && (
              <>
                <button
                  aria-label="Export Excel"
                  type="button"
                  onClick={() => handleExport("excel")}
                >
                  <FileSpreadsheet size={16} aria-hidden="true" />
                  <span>Excel</span>
                </button>
                <button
                  aria-label="Export PPT"
                  type="button"
                  onClick={() => handleExport("ppt")}
                >
                  <Presentation size={16} aria-hidden="true" />
                  <span>PPT</span>
                </button>
              </>
            )}
          </div>
        </header>

        {geoVisualMode === "globe" ? (
          <div className="geo-visual-layer geo-visual-layer-active" key="globe">
            {useMockGlobe ? (
              <>
                <canvas
                  className="globe-test-canvas"
                  width={stageSize.width}
                  height={stageSize.height}
                  aria-label="Mock 3D opportunity globe"
                />
                {useMockCluster ? (
                  <div className="marker-overlay-layer mock-cluster-layer" aria-label="Mock globe marker layer">
                    {mockScreenGlobeMarkers.map((marker) => (
                      <GlobeMarkerButton
                        key={marker.key}
                        marker={marker}
                        active={marker.kind === "city" && marker.key === selectedCityKey}
                        onSelect={handleGlobeMarkerSelect}
                        spiderChild={marker.spiderChild}
                        style={{
                          opacity: marker.visible ? 1 : 0,
                          visibility: marker.visible ? "visible" : "hidden",
                          pointerEvents: marker.visible ? "auto" : "none",
                          transform: `translate3d(${marker.x}px, ${marker.y}px, 0) translate(-50%, -50%)`,
                        }}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="mock-marker-layer" aria-label="Mock globe marker layer">
                    {visibleGlobeMarkers.map((marker) => (
                      <GlobeMarkerButton
                        key={marker.key}
                        marker={marker}
                        active={marker.kind === "city" && marker.key === selectedCityKey}
                        onSelect={handleGlobeMarkerSelect}
                      />
                    ))}
                  </div>
                )}
              </>
            ) : (
              <>
                <Globe
                  ref={globeRef}
                  width={stageSize.width}
                  height={stageSize.height}
                  backgroundColor="rgba(0,0,0,0)"
                  globeImageUrl={EARTH_IMAGE}
                  bumpImageUrl={EARTH_BUMP}
                  showAtmosphere
                  atmosphereColor="#55f1d1"
                  atmosphereAltitude={0.18}
                  polygonsData={countryPolygons}
                  polygonCapColor={(country) =>
                    polygonCapColor(country as CountryFeature, selectedCountry, hoveredCountry, countryNamesWithData)
                  }
                  polygonSideColor={(country) =>
                    countryName(country as CountryFeature) === selectedCountry
                      ? "rgba(255, 213, 122, 0.2)"
                      : "rgba(32, 50, 48, 0.18)"
                  }
                  polygonStrokeColor={(country) =>
                    countryName(country as CountryFeature) === selectedCountry
                      ? "rgba(255, 222, 135, 0.95)"
                      : countryNamesWithData.has(countryName(country as CountryFeature))
                        ? "rgba(74, 245, 212, 0.72)"
                        : "rgba(255, 255, 255, 0.16)"
                  }
                  polygonAltitude={(country) =>
                    countryName(country as CountryFeature) === selectedCountry ? 0.03 : 0.01
                  }
                  onPolygonClick={(country) => handleCountryClick(country as CountryFeature)}
                  onPolygonHover={(country) =>
                    setHoveredCountry(country ? countryName(country as CountryFeature) : "")
                  }
                  pointsData={[]}
                  labelsData={[]}
                  onGlobeReady={() => {
                    setGlobeReady(true);
                    const pointOfViewOverride = pointOfViewOverrideRef.current;
                    if (pointOfViewOverride) {
                      globeModeRef.current = "focused";
                      globeRef.current?.pointOfView({
                        lat: pointOfViewOverride.lat,
                        lng: pointOfViewOverride.lng,
                        altitude: OVERVIEW_ALTITUDE,
                      }, 0);
                      configureGlobeControls(globeRef.current, "focused");
                      return;
                    }
                    const center = selectedCityMarker
                      ? cityFocus(selectedCityMarker)
                      : selectedCountry
                        ? countryFocus(selectedCountry, features, countryPolygons)
                        : propertyFocus(features);
                    const mode: GlobeMode = selectedCityMarker || selectedCountry ? "focused" : "overview";
                    globeRef.current?.pointOfView({
                      lat: center.lat,
                      lng: center.lng,
                      altitude: selectedCityMarker
                        ? PROPERTY_ALTITUDE
                        : selectedCountry
                          ? COUNTRY_ALTITUDE
                          : OVERVIEW_ALTITUDE,
                    }, 0);
                    applyGlobeMode(mode);
                  }}
                />
                <div className="marker-overlay-layer" aria-label="Globe marker layer">
                  {screenGlobeMarkers.map((marker) => (
                    <GlobeMarkerButton
                      key={marker.key}
                      marker={marker}
                      active={marker.kind === "city" && marker.key === selectedCityKey}
                      onSelect={handleGlobeMarkerSelect}
                      spiderChild={marker.spiderChild}
                      style={{
                        opacity: marker.visible ? 1 : 0,
                        visibility: marker.visible ? "visible" : "hidden",
                        pointerEvents: marker.visible ? "auto" : "none",
                        transform: `translate3d(${marker.x}px, ${marker.y}px, 0) translate(-50%, -50%)`,
                      }}
                    />
                  ))}
                </div>
              </>
            )}
          </div>
        ) : (
          <React.Suspense fallback={<SatelliteNavigatorFallback mode={geoVisualMode} />}>
            <SatelliteNavigator
              mode={geoVisualMode}
              camera={satelliteCamera}
              markers={satelliteMarkers}
              stageSize={stageSize}
              mock={useMockSatellite}
              tileTemplate={runtimeConfig.map.satelliteTileTemplate}
              tileSize={runtimeConfig.map.satelliteTileSize}
              attribution={runtimeConfig.map.satelliteAttribution}
              onMarkerSelect={handleSatelliteMarkerSelect}
            />
          </React.Suspense>
        )}

        {showInitialThinking && <AIThinkingOverlay stage={aiThinkingStage} />}

        <CountryScopeControl
          summaries={summaries}
          selectedCountry={selectedCountry}
          loading={loading}
          isOpen={isCountryScopeOpen}
          query={countryQuery}
          onToggle={() => setIsCountryScopeOpen((current) => !current)}
          onQueryChange={setCountryQuery}
          onSelectCountry={handleCountrySelect}
        />

        <div className="status-hud" aria-live="polite">
          <span>
            {loading
              ? "Loading"
              : selectedCountry
                ? `${visibleGlobeMarkers.length} cities visible`
                : `${visibleGlobeMarkers.length} countries visible`}
          </span>
          {selectedCountry && <strong>{selectedCountry}</strong>}
          {selectedCityMarker && <strong>{selectedCityMarker.city}</strong>}
          {hoveredCountry && <span>{hoveredCountry}</span>}
          {discoveryStatus && (
            <span>
              Discovery {taskBacklogTotal(discoveryStatus.task_backlog)} tasks /{" "}
              {discoveryStatus.pending_evidence_count} new evidence
            </span>
          )}
        </div>
      </section>

      <aside
        className={`insight-panel ${viewMode === "overview" ? "" : "workspace-panel"}`}
        aria-label="Country opportunity panel"
      >
        <CountryPanel
          viewMode={viewMode}
          selectedCountry={selectedCountry}
          selectedCityMarker={selectedCityMarker}
          selectedSummary={panelSummary}
          regionSummaries={regionSummaries}
          countryCount={summaries.length}
          countryCityCount={selectedCityMarker ? 1 : selectedCountryCityCount}
          packets={panelPackets}
          packetsLoading={packetsLoading}
          selectedPropertyId={selectedPropertyId}
          panelSceneFilter={panelSceneFilter}
          contextLabel={workspaceContext}
          gapSummary={gapSummary}
          listMode={listMode}
          detailTab={detailTab}
          onListModeChange={setListMode}
          onSelectProperty={handlePropertySelect}
          onBackToList={handleBackToList}
          onClearCity={handleClearCity}
          onPanelSceneSelect={handlePanelSceneSelect}
          onSetDetailTab={setDetailTab}
        />
      </aside>

      {isRagOpen && enabledFeatures.rag && (
        <RagDrawer
          selectedCountry={selectedCountry}
          selectedPacket={selectedPacket}
          question={ragQuestion}
          result={ragResult}
          loading={ragLoading}
          error={ragError}
          onQuestionChange={setRagQuestion}
          onAsk={askRag}
          onClose={() => setIsRagOpen(false)}
        />
      )}

          {toast && <div className={`toast ${toast.tone}`}>{toast.message}</div>}
    </main>
  );
}

function CountryScopeControl({
  summaries,
  selectedCountry,
  loading,
  isOpen,
  query,
  onToggle,
  onQueryChange,
  onSelectCountry,
}: {
  summaries: CountrySummary[];
  selectedCountry: string;
  loading: boolean;
  isOpen: boolean;
  query: string;
  onToggle: () => void;
  onQueryChange: (value: string) => void;
  onSelectCountry: (country: string) => void;
}) {
  const sortedSummaries = useMemo(() => {
    return [...summaries].sort((a, b) => {
      if (b.map_point_count !== a.map_point_count) {
        return b.map_point_count - a.map_point_count;
      }
      return a.country.localeCompare(b.country);
    });
  }, [summaries]);
  const normalizedQuery = query.trim().toLowerCase();
  const filteredSummaries = normalizedQuery
    ? sortedSummaries.filter((summary) => summary.country.toLowerCase().includes(normalizedQuery))
    : sortedSummaries;
  const selectedSummary = summaries.find((summary) => summary.country === selectedCountry);
  const totalCandidateCount = summaries.reduce((total, summary) => total + summary.candidate_count, 0);
  const summaryLabel = selectedCountry
    ? `${selectedCountry} · ${
        loading ? "Loading" : `${formatNumber(selectedSummary?.candidate_count || 0)} candidates`
      }`
    : `All countries · ${summaries.length} countries · ${
        loading ? "Loading" : `${formatNumber(totalCandidateCount)} candidates`
      }`;

  return (
    <section className={`country-scope ${isOpen ? "open" : ""}`} aria-label="Country scope">
      <button
        className="country-scope-trigger"
        type="button"
        aria-expanded={isOpen}
        aria-controls="country-scope-menu"
        aria-label={`Country scope: ${summaryLabel}`}
        onClick={onToggle}
      >
        <MapPin size={16} aria-hidden="true" />
        <span>{summaryLabel}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {isOpen && (
        <div className="country-scope-menu" id="country-scope-menu">
          <label className="country-search">
            <Search size={14} aria-hidden="true" />
            <input
              aria-label="Country search"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              placeholder="Search countries"
              autoFocus
            />
          </label>
          <button
            className={`country-row all ${selectedCountry ? "" : "active"}`}
            type="button"
            onClick={() => onSelectCountry("")}
          >
            <span>
              All countries
              <small>{summaries.length} countries in database</small>
            </span>
            <strong>{formatNumber(totalCandidateCount)}</strong>
          </button>
          <div className="country-result-list">
            {filteredSummaries.map((summary) => (
              <button
                key={summary.country}
                className={`country-row ${summary.country === selectedCountry ? "active" : ""}`}
                type="button"
                onClick={() => onSelectCountry(summary.country)}
              >
                <span>
                  {summary.country}
                  <small>
                    {formatNumber(summary.candidate_count)} candidates ·{" "}
                    {formatNumber(summary.review_count)} review
                  </small>
                </span>
                <strong>{formatNumber(summary.map_point_count)}</strong>
              </button>
            ))}
            {filteredSummaries.length === 0 && (
              <div className="country-empty">No countries found</div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function RagDrawer({
  selectedCountry,
  selectedPacket,
  question,
  result,
  loading,
  error,
  onQuestionChange,
  onAsk,
  onClose,
}: {
  selectedCountry: string;
  selectedPacket: SitePacket | null;
  question: string;
  result: RagQueryResponse | null;
  loading: boolean;
  error: string;
  onQuestionChange: (value: string) => void;
  onAsk: () => void;
  onClose: () => void;
}) {
  const contextCountry = selectedPacket?.entity.country || selectedCountry || "All countries";
  const contextProperty = selectedPacket?.entity.property_name || "No property selected";
  const examplePrompts = selectedPacket
    ? [
        "Why is this property recommended?",
        "Which evidence supports this action class?",
        "What should the review queue verify next?",
      ]
    : [
        "Which properties need review first?",
        "Summarize evidence gaps for this scope.",
        "Show high-value opportunities by scene.",
      ];

  return (
    <>
      <button className="rag-scrim" type="button" aria-label="Close Ask iSite2" onClick={onClose} />
      <aside className="rag-drawer" role="dialog" aria-modal="true" aria-label="Ask iSite2 RAG assistant">
        <div className="rag-head">
          <div>
            <span className="eyebrow">RAG Entry</span>
            <h2>Ask iSite2</h2>
          </div>
          <button type="button" aria-label="Close Ask iSite2" onClick={onClose}>
            <X size={17} aria-hidden="true" />
          </button>
        </div>

        <div className="rag-context" aria-label="Current RAG context">
          <div>
            <span>Country</span>
            <strong>{contextCountry}</strong>
          </div>
          <div>
            <span>Property</span>
            <strong>{contextProperty}</strong>
          </div>
        </div>

        <div className="rag-examples" aria-label="Example RAG questions">
          {examplePrompts.map((prompt) => (
            <button key={prompt} type="button" onClick={() => onQuestionChange(prompt)}>
              {prompt}
            </button>
          ))}
        </div>

        <label className="rag-input">
          <span>Question</span>
          <textarea
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            placeholder="Ask about evidence, review queue, value class, or recommended action..."
          />
        </label>

        {error && (
          <div className="rag-disconnected error" role="alert">
            <Bot size={18} aria-hidden="true" />
            <div>
              <strong>Ask iSite2 failed</strong>
              <span>{error}</span>
            </div>
          </div>
        )}

        {result ? (
          <RagResult result={result} />
        ) : (
          <div className="rag-disconnected" role="status">
            <Bot size={18} aria-hidden="true" />
            <div>
              <strong>Evidence-backed retrieval ready</strong>
              <span>Answers will include citations and concrete review actions.</span>
            </div>
          </div>
        )}

        <button
          className="rag-send"
          type="button"
          disabled={!question.trim() || loading}
          onClick={onAsk}
        >
          <Bot size={16} aria-hidden="true" />
          <span>{loading ? "Retrieving" : "Send"}</span>
        </button>
      </aside>
    </>
  );
}

function RagResult({ result }: { result: RagQueryResponse }) {
  return (
    <div className="rag-result" aria-label="Ask iSite2 response">
      <section>
        <span>Answer</span>
        <p>{result.answer}</p>
      </section>
      {result.priority_recommendations.length > 0 && (
        <section>
          <span>Priority</span>
          <div className="rag-priority-list">
            {result.priority_recommendations.map((item) => (
              <article key={`${item.property_id || item.property_name}-${item.priority_band}`}>
                <strong>{item.property_name}</strong>
                <small>{item.priority_band}</small>
                <p>{item.rationale}</p>
              </article>
            ))}
          </div>
        </section>
      )}
      {result.review_actions.length > 0 && (
        <section>
          <span>Review Actions</span>
          <ul>
            {result.review_actions.slice(0, 4).map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <span>Citations</span>
        <div className="rag-citation-list">
          {result.citations.map((citation) => (
            <a
              key={citation.chunk_id}
              href={citation.source_url || "#"}
              target="_blank"
              rel="noreferrer"
            >
              <strong>{citation.source_name || citation.source_url || "Indexed evidence"}</strong>
              <small>
                {citation.source_tier || "Unknown tier"}
                {citation.source_date ? ` · ${citation.source_date}` : ""}
              </small>
              <p>{citation.excerpt}</p>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}

function AIThinkingOverlay({ stage }: { stage: string }) {
  return (
    <div className="ai-thinking-overlay" role="status" aria-live="polite">
      <div className="ai-thinking-card">
        <span className="ai-thinking-orbit" aria-hidden="true" />
        <div>
          <span className="eyebrow">AI Thinking</span>
          <strong>{stage}</strong>
          <small>Preparing the opportunity globe</small>
        </div>
        <span className="ai-thinking-scanline" aria-hidden="true" />
      </div>
    </div>
  );
}

function SatelliteNavigatorFallback({ mode }: { mode: GeoVisualMode }) {
  return (
    <div
      className="geo-visual-layer satellite-navigator satellite-navigator-loading"
      aria-label="Satellite opportunity map"
      data-satellite-mode={mode}
      data-tile-status="tiles-loading"
    >
      <div className="satellite-mock-canvas" aria-hidden="true" />
      <div className="satellite-shade" aria-hidden="true" />
      <div className="satellite-status">
        <span>{satelliteModeLabel(mode)}</span>
        <strong>Loading map engine</strong>
      </div>
    </div>
  );
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

function aiThinkingStageLabel({
  globeReady,
  overviewMarkersReady,
  overviewRegionReady,
  overviewSummaryReady,
}: {
  globeReady: boolean;
  overviewMarkersReady: boolean;
  overviewRegionReady: boolean;
  overviewSummaryReady: boolean;
}): string {
  if (!globeReady) {
    return "Initializing globe engine";
  }
  if (!overviewSummaryReady) {
    return "Loading country intelligence";
  }
  if (!overviewMarkersReady) {
    return "Projecting opportunity markers";
  }
  if (!overviewRegionReady) {
    return "Preparing regional brief";
  }
  return "Preparing regional brief";
}

function CountryPanel({
  viewMode,
  selectedCountry,
  selectedCityMarker,
  selectedSummary,
  regionSummaries,
  countryCount,
  countryCityCount,
  packets,
  packetsLoading,
  selectedPropertyId,
  panelSceneFilter,
  contextLabel,
  gapSummary,
  listMode,
  detailTab,
  onListModeChange,
  onSelectProperty,
  onBackToList,
  onClearCity,
  onPanelSceneSelect,
  onSetDetailTab,
}: {
  viewMode: ViewMode;
  selectedCountry: string;
  selectedCityMarker: CityMarker | null;
  selectedSummary: CountrySummary;
  regionSummaries: RegionSummary[];
  countryCount: number;
  countryCityCount: number;
  packets: SitePacket[];
  packetsLoading: boolean;
  selectedPropertyId: string;
  panelSceneFilter: string;
  contextLabel: string;
  gapSummary: EvidenceGapSummary;
  listMode: WorkspaceListMode;
  detailTab: "evidence" | "inference" | "review";
  onListModeChange: (mode: WorkspaceListMode) => void;
  onSelectProperty: (id: string) => void;
  onBackToList: () => void;
  onClearCity: () => void;
  onPanelSceneSelect: (scene: string) => void;
  onSetDetailTab: (tab: "evidence" | "inference" | "review") => void;
}) {
  const selectedPacket =
    packets.find((packet) => packet.entity.property_id === selectedPropertyId) ?? null;
  const title = selectedCityMarker?.city || selectedCountry || "All candidate countries";
  const isOverview = !selectedCountry && !selectedCityMarker;
  const kpis = isOverview
    ? [
        { label: "Countries", value: countryCount },
        { label: "Candidates", value: selectedSummary.candidate_count },
        { label: "Sources", value: selectedSummary.source_count },
      ]
    : [
        { label: "Cities", value: countryCityCount },
        { label: "Candidates", value: selectedSummary.candidate_count },
        { label: "Sources", value: selectedSummary.source_count },
      ];

  if (selectedPacket) {
    const entity = selectedPacket.entity;
    return (
      <div className="panel-scroll property-focus-panel" data-panel-mode={viewMode}>
        <WorkspaceContextBar
          title={entity.property_name}
          subtitle={contextLabel}
          kpis={kpis}
          gapSummary={gapSummary}
          listMode={listMode}
          onListModeChange={onListModeChange}
          showListToggle={false}
        />
        <button className="back-button" type="button" onClick={onBackToList}>
          <ArrowLeft size={16} aria-hidden="true" />
          <span>Back to list</span>
        </button>
        <div className="property-focus-head">
          <span className="eyebrow">Property Dossier</span>
          <h2>{entity.property_name}</h2>
          <div className="focus-meta">
            <span>{entity.country}</span>
            <span>{entity.city}</span>
            <span>{sceneLabel(entity.scene_type)}</span>
          </div>
        </div>
        <DetailDossier
          packet={selectedPacket}
          detailTab={detailTab}
          onSetDetailTab={onSetDetailTab}
        />
      </div>
    );
  }

  return (
    <div className="panel-scroll" data-panel-mode={viewMode}>
      {!isOverview && (
        <WorkspaceContextBar
          title={title}
          subtitle={contextLabel}
          kpis={kpis}
          gapSummary={gapSummary}
          listMode={listMode}
          onListModeChange={onListModeChange}
        />
      )}
      <div className="panel-head">
        <span className="eyebrow">Country Opportunities</span>
        <h2>{title}</h2>
        {selectedCityMarker && (
          <button className="city-clear-button" type="button" onClick={onClearCity}>
            <ArrowLeft size={15} aria-hidden="true" />
            <span>All cities in {selectedCityMarker.country}</span>
          </button>
        )}
        <div className="kpi-grid">
          {kpis.map((item) => (
            <Kpi key={item.label} label={item.label} value={item.value} />
          ))}
        </div>
      </div>

      <SceneDistribution
        scenes={selectedSummary.scenes}
        activeScene={panelSceneFilter}
        interactive={!isOverview}
        onSelectScene={onPanelSceneSelect}
      />

      {!isOverview && <EvidenceGapBand summary={gapSummary} />}

      {isOverview && <RegionDistribution regions={regionSummaries} />}

      {!isOverview && listMode === "card" && (
        <div className="property-list">
          {packets.map((packet) => (
            <PropertyCard
              key={packet.entity.property_id}
              packet={packet}
              active={packet.entity.property_id === selectedPropertyId}
              onClick={() => onSelectProperty(packet.entity.property_id)}
            />
          ))}
        </div>
      )}

      {!isOverview && listMode === "dense" && (
        <DensePropertyList
          packets={packets}
          activePropertyId={selectedPropertyId}
          onSelectProperty={onSelectProperty}
        />
      )}

      {!isOverview && packets.length === 0 && packetsLoading && (
        <div className="empty-panel">
          <MapPin size={20} aria-hidden="true" />
          <strong>AI Thinking</strong>
          <span>Map markers are ready while the full evidence packet list loads.</span>
        </div>
      )}

      {packets.length === 0 && !packetsLoading && !isOverview && (
        <div className="empty-panel">
          <MapPin size={20} aria-hidden="true" />
          <strong>{selectedCountry ? "No visible points" : "No scan data"}</strong>
          <span>
            {selectedCountry
              ? "Adjust filters or return to all countries."
              : "No candidate properties are currently stored in the database."}
          </span>
        </div>
      )}
    </div>
  );
}

function WorkspaceContextBar({
  title,
  subtitle,
  kpis,
  gapSummary,
  listMode,
  onListModeChange,
  showListToggle = true,
}: {
  title: string;
  subtitle: string;
  kpis: Array<{ label: string; value: number }>;
  gapSummary: EvidenceGapSummary;
  listMode: WorkspaceListMode;
  onListModeChange: (mode: WorkspaceListMode) => void;
  showListToggle?: boolean;
}) {
  return (
    <section className="workspace-context-bar" aria-label="Workspace context">
      <div className="workspace-context-title">
        <span className="eyebrow">Workspace</span>
        <strong>{title}</strong>
        <small>{subtitle}</small>
      </div>
      <div className="workspace-context-kpis">
        {kpis.map((item) => (
          <span key={item.label}>
            <small>{item.label}</small>
            <strong>{formatNumber(item.value)}</strong>
          </span>
        ))}
        <span>
          <small>Review</small>
          <strong>{formatNumber(gapSummary.reviewQueueCount)}</strong>
        </span>
      </div>
      {showListToggle && (
        <div className="list-mode-toggle" aria-label="Property list display mode">
          <button
            className={listMode === "card" ? "active" : ""}
            type="button"
            aria-pressed={listMode === "card"}
            aria-label="Card view"
            onClick={() => onListModeChange("card")}
          >
            <LayoutGrid size={15} aria-hidden="true" />
            <span>Card</span>
          </button>
          <button
            className={listMode === "dense" ? "active" : ""}
            type="button"
            aria-pressed={listMode === "dense"}
            aria-label="Dense view"
            onClick={() => onListModeChange("dense")}
          >
            <List size={15} aria-hidden="true" />
            <span>Dense</span>
          </button>
        </div>
      )}
    </section>
  );
}

function EvidenceGapBand({ summary }: { summary: EvidenceGapSummary }) {
  const items = [
    { label: "No metric", value: summary.missingPrimaryMetricCount },
    { label: "No image", value: summary.missingImageCount },
    { label: "Low evidence", value: summary.lowEvidenceCount },
    { label: "Coordinate", value: summary.coordinateReviewCount },
    { label: "Review", value: summary.reviewQueueCount },
  ];
  return (
    <section className="evidence-gap-band" aria-label="Evidence gaps">
      <span>Evidence gaps</span>
      {items.map((item) => (
        <strong key={item.label}>
          {item.label}
          <b>{formatNumber(item.value)}</b>
        </strong>
      ))}
    </section>
  );
}

function Kpi({ label, value }: { label: string; value: number }) {
  return (
    <div className="kpi">
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
    </div>
  );
}

function SceneDistribution({
  scenes,
  activeScene = "",
  interactive = false,
  onSelectScene,
}: {
  scenes: Record<string, number>;
  activeScene?: string;
  interactive?: boolean;
  onSelectScene?: (scene: string) => void;
}) {
  const entries = Object.entries(scenes);
  const total = entries.reduce((sum, [, value]) => sum + value, 0) || 1;
  return (
    <section className="scene-band" aria-label="Scene distribution">
      {entries.map(([scene, count]) => {
        const isActive = activeScene === scene;
        const content = (
          <>
            <span>{sceneLabel(scene)}</span>
            <div className="bar-track">
              <div style={{ width: `${Math.max(10, (count / total) * 100)}%` }} />
            </div>
            <strong>{count}</strong>
          </>
        );
        if (interactive && onSelectScene) {
          return (
            <button
              key={scene}
              className={`scene-row ${isActive ? "active" : ""}`}
              type="button"
              aria-pressed={isActive}
              onClick={() => onSelectScene(scene)}
            >
              {content}
            </button>
          );
        }
        return (
          <div key={scene} className="scene-row">
            {content}
          </div>
        );
      })}
    </section>
  );
}

function RegionDistribution({ regions }: { regions: RegionSummary[] }) {
  if (regions.length === 0) {
    return null;
  }
  return (
    <section className="region-band" aria-label="Region distribution">
      <div className="region-band-head">
        <span>Region distribution</span>
        <strong>{formatNumber(regions.reduce((total, region) => total + region.country_count, 0))} countries</strong>
      </div>
      {regions.map((region) => (
        <div className="region-row" key={region.region}>
          <div>
            <strong>{region.region}</strong>
            <span>
              {formatNumber(region.country_count)} countries · {formatNumber(region.source_count)} sources
            </span>
          </div>
          <div className="region-metrics">
            <span>
              <strong>{formatNumber(region.candidate_count)}</strong>
              Candidates
            </span>
            <span>
              <strong>{formatNumber(region.source_count)}</strong>
              Sources
            </span>
          </div>
        </div>
      ))}
    </section>
  );
}

function DensePropertyList({
  packets,
  activePropertyId,
  onSelectProperty,
}: {
  packets: SitePacket[];
  activePropertyId: string;
  onSelectProperty: (id: string) => void;
}) {
  return (
    <div className="dense-property-list" aria-label="Dense property list">
      <div className="dense-property-head" aria-hidden="true">
        <span>Property</span>
        <span>Scene</span>
        <span>Primary metric</span>
        <span>Evidence</span>
        <span>Sources</span>
        <span>Review</span>
        <span>Action</span>
      </div>
      {packets.map((packet) => {
        const entity = packet.entity;
        const metric = primaryMetricEvidence(packet)?.field_value || "No primary metric";
        const sourceCount = evidenceSourceCount(packet);
        return (
          <button
            key={entity.property_id}
            className={`dense-property-row ${entity.property_id === activePropertyId ? "active" : ""}`}
            type="button"
            aria-pressed={entity.property_id === activePropertyId}
            onClick={() => onSelectProperty(entity.property_id)}
          >
            <span>
              <strong>{entity.property_name}</strong>
              <small>{entity.city}</small>
            </span>
            <span>{sceneLabel(entity.scene_type)}</span>
            <span>{metric}</span>
            <span>{packet.conclusion.evidence_status}</span>
            <span>{formatNumber(sourceCount)}</span>
            <span>{formatNumber(packet.review_queue.length)}</span>
            <span>{packet.conclusion.action_class}</span>
          </button>
        );
      })}
    </div>
  );
}

function PropertyCard({
  packet,
  active,
  onClick,
}: {
  packet: SitePacket;
  active: boolean;
  onClick: () => void;
}) {
  const entity = packet.entity;
  const evidenceUrls = new Set(packet.evidence.map((item) => item.source_url));
  const image = entity.hero_image;
  const metric = primaryMetricEvidence(packet)?.field_value || "No primary metric";
  return (
    <article className={`property-card ${active ? "active" : ""}`}>
      <button type="button" onClick={onClick} aria-pressed={active}>
        <HeroMedia image={image} propertyName={entity.property_name} />
        <div className="card-body">
          <div className="card-title-row">
            <h3>{entity.property_name}</h3>
            <span>{sceneLabel(entity.scene_type)}</span>
          </div>
          <p>{metric}</p>
          <div className="card-meta">
            <span>{entity.city}</span>
            <span>{packet.conclusion.value_class}</span>
            <span>{packet.conclusion.action_class}</span>
          </div>
          <div className="card-foot">
            <span>{entity.geocode_precision}</span>
            <strong>{evidenceUrls.size} sources</strong>
          </div>
        </div>
      </button>
      {image && (
        <a className="image-source" href={image.source_url} target="_blank" rel="noreferrer">
          <ExternalLink size={13} aria-hidden="true" />
          <span>{image.source_name}</span>
        </a>
      )}
    </article>
  );
}

function HeroMedia({ image, propertyName }: { image?: HeroImage | null; propertyName: string }) {
  const [failed, setFailed] = useState(false);
  if (!image || failed) {
    return (
      <div className="hero-fallback">
        <ImageOff size={22} aria-hidden="true" />
        <span>{propertyName}</span>
      </div>
    );
  }
  return (
    <img
      src={image.url}
      alt={image.alt_text || propertyName}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

function DetailDossier({
  packet,
  detailTab,
  onSetDetailTab,
}: {
  packet: SitePacket;
  detailTab: "evidence" | "inference" | "review";
  onSetDetailTab: (tab: "evidence" | "inference" | "review") => void;
}) {
  const entity = packet.entity;
  const primaryMetric = primaryMetricEvidence(packet)?.field_value || "Missing primary metric";
  const sourceCount = evidenceSourceCount(packet);
  return (
    <section className="dossier cockpit-dossier">
      <div className="dossier-hero">
        <div>
          <span className="eyebrow">Investment cockpit</span>
          <h3>{entity.property_name}</h3>
          <p>{packet.conclusion.reason_to_recommend}</p>
        </div>
        <a href={entity.google_maps_link || "#"} target="_blank" rel="noreferrer">
          <MapPin size={15} aria-hidden="true" />
          <span>Google Maps</span>
        </a>
      </div>

      <div className="dossier-cockpit-grid">
        <article className="primary-metric-card">
          <span>{primaryMetricFilterLabel(entity.scene_type)}</span>
          <strong>{primaryMetric}</strong>
          <small>{sceneLabel(entity.scene_type)}</small>
        </article>
        <article className="decision-card">
          <span>Action Class</span>
          <strong>{packet.conclusion.action_class}</strong>
          <small>{packet.conclusion.value_class} · {packet.conclusion.recommended_solution}</small>
        </article>
        <article className="decision-card next-action-card">
          <span>Next Action</span>
          <strong>{packet.conclusion.next_action}</strong>
        </article>
      </div>

      <div className="dossier-status-strip">
        <DossierStatusPill label="Evidence" value={packet.conclusion.evidence_status} />
        <DossierStatusPill label="Sources" value={formatNumber(sourceCount)} />
        <DossierStatusPill label="Review" value={formatNumber(packet.review_queue.length)} />
        <DossierStatusPill label="Coordinate" value={entity.coordinate_status} />
        <DossierStatusPill label="Indoor RAT" value={packet.build_status.indoor_rat} />
      </div>

      <div className="metric-grid network-metric-grid">
        <Metric label="Annual Visits" value={formatNumber(packet.scene.annual_visits_est)} />
        <Metric label="Busy Users" value={formatNumber(packet.demand?.busy_hour_users)} />
        <Metric label="Busy Traffic GB" value={formatNumber(packet.demand?.busy_hour_traffic_gb)} />
        <Metric label="Bandwidth Mbps" value={formatNumber(packet.demand?.busy_hour_bandwidth_mbps)} />
        <Metric label="Map Source" value={entity.map_source || "Unknown"} />
      </div>

      <div className="tabs">
        {(["evidence", "inference", "review"] as const).map((tab) => (
          <button
            key={tab}
            className={detailTab === tab ? "active" : ""}
            type="button"
            onClick={() => onSetDetailTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>
      {detailTab === "evidence" && (
        <RecordList
          items={packet.evidence}
          render={(item) => (
            <>
              <strong>{item.field_group}</strong>
              <p>{item.field_value}</p>
              <a href={item.source_url} target="_blank" rel="noreferrer">
                {item.source_name} · {item.source_tier}
              </a>
            </>
          )}
        />
      )}
      {detailTab === "inference" && (
        <RecordList
          items={packet.inference}
          render={(item) => (
            <>
              <strong>{item.inferred_field}</strong>
              <p>{item.inference_chain}</p>
              <span>{item.inference_confidence}</span>
            </>
          )}
        />
      )}
      {detailTab === "review" && (
        <RecordList
          items={packet.review_queue}
          render={(item) => (
            <>
              <strong>{item.reason}</strong>
              <p>{item.next_action}</p>
              <span>{item.status}</span>
            </>
          )}
        />
      )}
    </section>
  );
}

function DossierStatusPill({ label, value }: { label: string; value: string | number }) {
  return (
    <span className="dossier-status-pill">
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function Metric({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value ?? "Unknown"}</strong>
    </div>
  );
}

function RecordList<T>({ items, render }: { items: T[]; render: (item: T) => React.ReactNode }) {
  if (items.length === 0) {
    return <div className="record-list empty">No rows</div>;
  }
  return <div className="record-list">{items.map((item, index) => <article key={index}>{render(item)}</article>)}</div>;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

function normalizeRuntimeConfig(config: RuntimeConfig | null | undefined): RuntimeConfig {
  return {
    mode: config?.mode || DEFAULT_RUNTIME_CONFIG.mode,
    features: {
      exports: config?.features?.exports ?? DEFAULT_RUNTIME_CONFIG.features.exports,
      rag: config?.features?.rag ?? DEFAULT_RUNTIME_CONFIG.features.rag,
      connectors: config?.features?.connectors ?? DEFAULT_RUNTIME_CONFIG.features.connectors,
      geocode: config?.features?.geocode ?? DEFAULT_RUNTIME_CONFIG.features.geocode,
    },
    map: {
      satelliteTileTemplate:
        config?.map?.satelliteTileTemplate || DEFAULT_RUNTIME_CONFIG.map.satelliteTileTemplate,
      satelliteTileSize:
        typeof config?.map?.satelliteTileSize === "number" && config.map.satelliteTileSize > 0
          ? config.map.satelliteTileSize
          : DEFAULT_RUNTIME_CONFIG.map.satelliteTileSize,
      satelliteAttribution:
        config?.map?.satelliteAttribution || DEFAULT_RUNTIME_CONFIG.map.satelliteAttribution,
    },
  };
}

function loadCountryPolygons(): CountryFeature[] {
  const collection = topoFeature(
    countries110m as unknown as Parameters<typeof topoFeature>[0],
    (countries110m as { objects: { countries: unknown } }).objects.countries as never,
  ) as unknown as FeatureCollection<Geometry, { name?: string }>;
  return collection.features.filter((item) => Boolean(countryName(item)));
}

function countryName(country: CountryFeature | null | undefined): string {
  return country?.properties?.name || "";
}

function polygonCapColor(
  country: CountryFeature,
  selectedCountry: string,
  hoveredCountry: string,
  countriesWithData: Set<string>,
): string {
  const name = countryName(country);
  if (name === selectedCountry) {
    return "rgba(255, 211, 110, 0.58)";
  }
  if (name === hoveredCountry) {
    return "rgba(96, 226, 193, 0.42)";
  }
  if (countriesWithData.has(name)) {
    return "rgba(32, 206, 177, 0.28)";
  }
  return "rgba(255, 255, 255, 0.055)";
}

function configureGlobeControls(globe: GlobeMethods | null, mode: GlobeMode) {
  const controls = globe?.controls();
  if (!controls) {
    return;
  }
  controls.enableDamping = true;
  controls.autoRotate = mode === "overview";
  controls.autoRotateSpeed = OVERVIEW_ROTATE_SPEED;
}

function aggregateCountryMarkers(
  summaries: CountrySummary[],
  features: MapFeature[],
  displayPositions: Map<string, { lat: number; lng: number; positionSource: CountryPositionSource }>,
): CountryMarker[] {
  type CountryAccumulator = CountryMarker & {
    latTotal: number;
    lngTotal: number;
    mappedCities: Set<string>;
  };

  const countryMap = new Map<string, CountryAccumulator>();
  summaries.forEach((summary) => {
    const key = countryKey(summary.country);
    countryMap.set(key, {
      kind: "country" as const,
      key,
      country: summary.country,
      label: summary.country,
      lat: 0,
      lng: 0,
      positionSource: "default_fallback",
      latTotal: 0,
      lngTotal: 0,
      candidateCount: summary.candidate_count,
      mapReadyCount: summary.map_point_count,
      mappedCityCount: 0,
      reviewCount: summary.review_count,
      sourceCount: summary.source_count,
      scenes: { ...summary.scenes },
      propertyIds: [],
      mappedCities: new Set<string>(),
    });
  });

  features.forEach((feature) => {
    const country = feature.properties.country;
    const key = countryKey(country);
    const current = countryMap.get(key);
    if (!current) {
      return;
    }
    const [lng, lat] = feature.geometry.coordinates;
    current.lngTotal += lng;
    current.latTotal += lat;
    current.propertyIds.push(feature.properties.property_id);
    current.mappedCities.add(cityKey(country, feature.properties.city));
    const mappedCount = current.propertyIds.length;
    current.lng = current.lngTotal / mappedCount;
    current.lat = current.latTotal / mappedCount;
    current.positionSource = "property_average";
  });

  return Array.from(countryMap.values())
    .map(({ latTotal: _latTotal, lngTotal: _lngTotal, mappedCities, ...marker }) => {
      const center = marker.propertyIds.length > 0
        ? {
            lat: marker.lat,
            lng: marker.lng,
            positionSource: "property_average" as const,
          }
        : displayPositions.get(marker.country) || defaultCountryDisplayPosition(marker.country);
      return {
        ...marker,
        lat: center.lat,
        lng: center.lng,
        positionSource: center.positionSource,
        mappedCityCount: mappedCities.size,
      };
    })
    .sort(compareMarkers);
}

function aggregateCityMarkers(
  packets: SitePacket[],
  features: MapFeature[],
  cityCentroidCache: Map<string, CityCentroidCacheValue>,
): CityMarker[] {
  type CityAccumulator = Omit<CityMarker, "sourceCount" | "positionSource"> & {
    latTotal: number;
    lngTotal: number;
    fallbackLatTotal: number;
    fallbackLngTotal: number;
    fallbackCount: number;
    sourceUrls: Set<string>;
  };

  const featureByPropertyId = mapFeaturesByPropertyId(features);
  const cityMap = new Map<string, CityAccumulator>();
  packets.forEach((packet) => {
    const entity = packet.entity;
    if (!entity.city.trim()) {
      return;
    }
    const key = cityKey(entity.country, entity.city);
    const current = cityMap.get(key) ?? {
      kind: "city" as const,
      key,
      country: entity.country,
      city: entity.city,
      label: entity.city,
      lat: 0,
      lng: 0,
      latTotal: 0,
      lngTotal: 0,
      fallbackLatTotal: 0,
      fallbackLngTotal: 0,
      fallbackCount: 0,
      candidateCount: 0,
      mapReadyCount: 0,
      reviewCount: 0,
      scenes: {},
      propertyIds: [],
      sourceUrls: new Set<string>(),
    };

    current.candidateCount += 1;
    current.reviewCount += packet.review_queue.length;
    current.scenes[entity.scene_type] = (current.scenes[entity.scene_type] || 0) + 1;
    current.propertyIds.push(entity.property_id);
    packet.evidence.forEach((item) => current.sourceUrls.add(item.source_url));

    if (Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude)) {
      current.fallbackLngTotal += entity.longitude;
      current.fallbackLatTotal += entity.latitude;
      current.fallbackCount += 1;
    }

    const feature = featureByPropertyId.get(entity.property_id);
    if (feature) {
      const [lng, lat] = feature.geometry.coordinates;
      current.lngTotal += lng;
      current.latTotal += lat;
      current.mapReadyCount += 1;
      current.lng = current.lngTotal / current.mapReadyCount;
      current.lat = current.latTotal / current.mapReadyCount;
    } else if (isMapReadyEntity(entity) && Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude)) {
      current.lngTotal += entity.longitude;
      current.latTotal += entity.latitude;
      current.mapReadyCount += 1;
      current.lng = current.lngTotal / current.mapReadyCount;
      current.lat = current.latTotal / current.mapReadyCount;
    }

    cityMap.set(key, current);
  });

  return Array.from(cityMap.values())
    .map(({
      latTotal: _latTotal,
      lngTotal: _lngTotal,
      fallbackLatTotal,
      fallbackLngTotal,
      fallbackCount,
      sourceUrls,
      ...marker
    }): CityMarker | null => {
      if (marker.mapReadyCount > 0) {
        return {
          ...marker,
          positionSource: "map_ready_average" as const,
          sourceCount: sourceUrls.size,
        };
      }
      if (fallbackCount > 0) {
        return {
          ...marker,
          lat: fallbackLatTotal / fallbackCount,
          lng: fallbackLngTotal / fallbackCount,
          positionSource: "property_average" as const,
          sourceCount: sourceUrls.size,
        };
      }
      const centroid = cityCentroidCache.get(marker.key);
      if (!isUsableGeocodeResult(centroid)) {
        return null;
      }
      return {
        ...marker,
        lat: centroid.latitude,
        lng: centroid.longitude,
        positionSource: "city_geocode" as const,
        sourceCount: sourceUrls.size,
      };
    })
    .filter((marker): marker is CityMarker => marker !== null)
    .sort(compareMarkers);
}

function aggregateCitySummaryMarkers(summaries: CitySummary[]): CityMarker[] {
  return summaries
    .filter((summary) => {
      return summary.city.trim()
        && Number.isFinite(summary.lat)
        && Number.isFinite(summary.lng);
    })
    .map((summary) => ({
      kind: "city" as const,
      key: cityKey(summary.country, summary.city),
      country: summary.country,
      city: summary.city,
      label: summary.city,
      lat: summary.lat,
      lng: summary.lng,
      positionSource: summary.position_source || "property_average",
      candidateCount: summary.candidate_count,
      mapReadyCount: summary.map_point_count,
      reviewCount: summary.review_count,
      sourceCount: summary.source_count,
      scenes: summary.scenes || {},
      propertyIds: summary.property_ids || [],
    }))
    .sort(compareMarkers);
}

function mapFeaturesByPropertyId(features: MapFeature[]): Map<string, MapFeature> {
  return new Map(features.map((feature) => [feature.properties.property_id, feature]));
}

function citiesNeedingCentroids(
  packets: SitePacket[],
  features: MapFeature[],
  selectedCountry: string,
  cityCentroidCache: Map<string, CityCentroidCacheValue>,
): Array<{ key: string; country: string; city: string }> {
  const featureByPropertyId = mapFeaturesByPropertyId(features);
  const cityMap = new Map<string, { key: string; country: string; city: string; mapReadyCount: number }>();
  packets.forEach((packet) => {
    const entity = packet.entity;
    if (entity.country !== selectedCountry || !entity.city.trim()) {
      return;
    }
    const key = cityKey(entity.country, entity.city);
    const current = cityMap.get(key) ?? {
      key,
      country: entity.country,
      city: entity.city,
      mapReadyCount: 0,
    };
    if (
      featureByPropertyId.has(entity.property_id)
      || (isMapReadyEntity(entity) && Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude))
    ) {
      current.mapReadyCount += 1;
    }
    cityMap.set(key, current);
  });
  return Array.from(cityMap.values()).filter((city) => {
    return city.mapReadyCount === 0 && !cityCentroidCache.has(city.key);
  });
}

function isUsableGeocodeResult(value: CityCentroidCacheValue | undefined): value is GeocodeResult {
  return Boolean(
    value
      && value !== "missing"
      && value !== "loading"
      && Number.isFinite(value.latitude)
      && Number.isFinite(value.longitude),
  );
}

function isMapReadyEntity(entity: Entity): boolean {
  return entity.coordinate_status.toLowerCase() === "map ready";
}

function deriveViewMode(
  selectedCountry: string,
  selectedCityKey: string,
  selectedPropertyId: string,
): ViewMode {
  if (selectedPropertyId) {
    return "property";
  }
  if (selectedCityKey) {
    return "city";
  }
  if (selectedCountry) {
    return "country";
  }
  return "overview";
}

function deriveGeoVisualMode(viewMode: ViewMode): GeoVisualMode {
  if (viewMode === "property") {
    return "property_satellite";
  }
  if (viewMode === "city") {
    return "city_satellite";
  }
  if (viewMode === "country") {
    return "country_satellite";
  }
  return "globe";
}

function satelliteCameraForScope({
  mode,
  selectedCountry,
  selectedCityMarker,
  selectedPacket,
  cityMarkers,
  visiblePackets,
  metricFilteredPackets,
  features,
  countryPolygons,
}: {
  mode: GeoVisualMode;
  selectedCountry: string;
  selectedCityMarker: CityMarker | null;
  selectedPacket: SitePacket | null;
  cityMarkers: CityMarker[];
  visiblePackets: SitePacket[];
  metricFilteredPackets: SitePacket[];
  features: MapFeature[];
  countryPolygons: CountryFeature[];
}): SatelliteCamera {
  if (mode === "property_satellite" && selectedPacket && isFiniteCoordinate(selectedPacket.entity)) {
    return {
      center: [selectedPacket.entity.longitude, selectedPacket.entity.latitude],
      zoom: 17,
      pitch: 60,
      bearing: -22,
    };
  }

  if (mode === "city_satellite") {
    const bounds = satelliteBoundsFromPackets(visiblePackets);
    if (bounds) {
      return cameraFromBounds(bounds, { pitch: 52, bearing: -18, minZoom: 11, maxZoom: 14.5 });
    }
    if (selectedCityMarker) {
      return {
        center: [selectedCityMarker.lng, selectedCityMarker.lat],
        zoom: 12.5,
        pitch: 52,
        bearing: -18,
      };
    }
  }

  if (mode === "country_satellite") {
    const cityBounds = satelliteBoundsFromMarkers(
      cityMarkers.filter((marker) => marker.country === selectedCountry),
    );
    const propertyBounds = satelliteBoundsFromPackets(
      metricFilteredPackets.filter((packet) => packet.entity.country === selectedCountry),
    );
    const bounds = cityBounds || propertyBounds;
    if (bounds) {
      return cameraFromBounds(bounds, { pitch: 45, bearing: -14, minZoom: 4.4, maxZoom: 7.6 });
    }
    const center = countryFocus(selectedCountry, features, countryPolygons);
    return {
      center: [center.lng, center.lat],
      zoom: 5.4,
      pitch: 45,
      bearing: -14,
    };
  }

  const center = propertyFocus(features);
  return {
    center: [center.lng, center.lat],
    zoom: 2.8,
    pitch: 0,
    bearing: 0,
  };
}

function satelliteMarkersForScope({
  mode,
  selectedCountry,
  selectedCityKey,
  selectedPropertyId,
  selectedPacket,
  cityMarkers,
  visiblePackets,
  metricFilteredPackets,
}: {
  mode: GeoVisualMode;
  selectedCountry: string;
  selectedCityKey: string;
  selectedPropertyId: string;
  selectedPacket: SitePacket | null;
  cityMarkers: CityMarker[];
  visiblePackets: SitePacket[];
  metricFilteredPackets: SitePacket[];
}): SatelliteMarker[] {
  if (mode === "country_satellite") {
    const cities = cityMarkers.filter((marker) => marker.country === selectedCountry);
    if (cities.length > 0) {
      return cities.map((marker) => ({
        id: marker.key,
        kind: "city",
        lat: marker.lat,
        lng: marker.lng,
        label: marker.city,
        country: marker.country,
        city: marker.city,
        count: marker.candidateCount,
        selected: marker.key === selectedCityKey,
        meta: `${formatNumber(marker.candidateCount)} candidates`,
        candidateCount: marker.candidateCount,
        sourceCount: marker.sourceCount,
        reviewCount: marker.reviewCount,
        scenes: marker.scenes,
      }));
    }
    return [];
  }
  if (mode === "city_satellite") {
    return visiblePackets
      .filter((packet) => isFiniteCoordinate(packet.entity))
      .map((packet) => propertySatelliteMarker(packet, selectedPropertyId));
  }
  if (mode === "property_satellite" && selectedPacket && isFiniteCoordinate(selectedPacket.entity)) {
    return [propertySatelliteMarker(selectedPacket, selectedPacket.entity.property_id)];
  }
  return [];
}

function propertySatelliteMarker(packet: SitePacket, selectedPropertyId: string): SatelliteMarker {
  const sourceCount = new Set(
    packet.evidence
      .map((item) => item.source_url || item.source_name)
      .filter(Boolean),
  ).size;
  return {
    id: packet.entity.property_id,
    kind: "property",
    lat: packet.entity.latitude,
    lng: packet.entity.longitude,
    label: packet.entity.property_name,
    country: packet.entity.country,
    city: packet.entity.city,
    selected: packet.entity.property_id === selectedPropertyId,
    meta: sceneLabel(packet.entity.scene_type),
    candidateCount: 1,
    sourceCount,
    reviewCount: packet.review_queue.length,
    scenes: { [packet.entity.scene_type]: 1 },
  };
}

function satelliteBoundsFromPackets(packets: SitePacket[]): SatelliteBounds | null {
  return boundsFromCoordinates(
    packets
      .filter((packet) => isFiniteCoordinate(packet.entity))
      .map((packet) => ({ lat: packet.entity.latitude, lng: packet.entity.longitude })),
  );
}

function satelliteBoundsFromMarkers(markers: CityMarker[]): SatelliteBounds | null {
  return boundsFromCoordinates(markers.map((marker) => ({ lat: marker.lat, lng: marker.lng })));
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

function isFiniteCoordinate(entity: Entity): boolean {
  return Number.isFinite(entity.latitude) && Number.isFinite(entity.longitude);
}

function summarizeEvidenceGaps(packets: SitePacket[]): EvidenceGapSummary {
  return packets.reduce<EvidenceGapSummary>(
    (summary, packet) => {
      if (!primaryMetricEvidence(packet)) {
        summary.missingPrimaryMetricCount += 1;
      }
      if (!packet.entity.hero_image) {
        summary.missingImageCount += 1;
      }
      if (packet.conclusion.evidence_status === "Insufficient") {
        summary.lowEvidenceCount += 1;
      }
      if (packet.entity.coordinate_status === "Review Required") {
        summary.coordinateReviewCount += 1;
      }
      summary.reviewQueueCount += packet.review_queue.length;
      return summary;
    },
    {
      missingPrimaryMetricCount: 0,
      missingImageCount: 0,
      lowEvidenceCount: 0,
      coordinateReviewCount: 0,
      reviewQueueCount: 0,
    },
  );
}

function workspaceContextLabel({
  selectedCountry,
  selectedCityMarker,
  panelSceneFilter,
  sceneFilter,
  evidenceFilter,
  actionFilter,
  reviewOnly,
}: {
  selectedCountry: string;
  selectedCityMarker: CityMarker | null;
  panelSceneFilter: string;
  sceneFilter: string;
  evidenceFilter: string;
  actionFilter: string;
  reviewOnly: boolean;
}): string {
  const parts = [
    selectedCityMarker
      ? `${selectedCityMarker.city}, ${selectedCityMarker.country}`
      : selectedCountry || "All countries",
  ];
  if (panelSceneFilter) {
    parts.push(`${sceneLabel(panelSceneFilter)} list`);
  }
  if (sceneFilter) {
    parts.push(`${sceneLabel(sceneFilter)} API filter`);
  }
  if (evidenceFilter) {
    parts.push(evidenceFilter);
  }
  if (actionFilter) {
    parts.push(actionFilter);
  }
  if (reviewOnly) {
    parts.push("Review only");
  }
  return parts.join(" · ");
}

function evidenceSourceCount(packet: SitePacket): number {
  return new Set(packet.evidence.map((item) => item.source_url)).size;
}

function filterAndSortPanelPackets(packets: SitePacket[], scene: string): SitePacket[] {
  if (!scene) {
    return packets;
  }
  return packets
    .filter((packet) => packet.entity.scene_type === scene)
    .slice()
    .sort(comparePacketsByPrimaryMetricDesc);
}

function comparePacketsByPrimaryMetricDesc(a: SitePacket, b: SitePacket): number {
  const aValue = primaryMetricValue(a);
  const bValue = primaryMetricValue(b);
  if (aValue !== null && bValue !== null && aValue !== bValue) {
    return bValue - aValue;
  }
  if (aValue !== null && bValue === null) {
    return -1;
  }
  if (aValue === null && bValue !== null) {
    return 1;
  }
  const nameCompare = a.entity.property_name.localeCompare(b.entity.property_name);
  if (nameCompare !== 0) {
    return nameCompare;
  }
  return a.entity.city.localeCompare(b.entity.city);
}

function primaryMetricValue(packet: SitePacket): number | null {
  const metricEvidence = primaryMetricEvidence(packet);
  if (!metricEvidence) {
    return null;
  }
  return numericMetricValue(metricEvidence.field_value);
}

function primaryMetricEvidence(packet: SitePacket): EvidenceItem | null {
  const config = SCENE_PRIMARY_METRIC_FILTERS[packet.entity.scene_type];
  const candidateEvidence = config
    ? packet.evidence.filter((item) => evidenceMatchesPrimaryMetric(item, config))
    : packet.evidence;
  const rankedEvidence = candidateEvidence
    .map((item, index) => ({
      item,
      index,
      rank: metricIndicatorRank(item, config),
      value: numericMetricValue(item.field_value),
    }))
    .filter((entry) => entry.value !== null && Number.isFinite(entry.value));
  if (rankedEvidence.length === 0) {
    return null;
  }
  rankedEvidence.sort((a, b) =>
    a.rank - b.rank
    || (b.value ?? 0) - (a.value ?? 0)
    || a.index - b.index,
  );
  return rankedEvidence[0].item;
}

function metricIndicatorRank(
  evidence: EvidenceItem,
  config: ScenePrimaryMetricFilter | undefined,
): number {
  if (!config) {
    return 0;
  }
  const keys = [
    normalizeMetricKey(evidence.field_group),
    normalizeMetricKey(evidence.indicator_name || ""),
  ];
  const ranks = config.indicators.map(normalizeMetricKey);
  const matched = keys
    .map((key) => ranks.indexOf(key))
    .filter((index) => index >= 0);
  return matched.length > 0 ? Math.min(...matched) : ranks.length;
}

function evidenceMatchesPrimaryMetric(
  evidence: EvidenceItem,
  config: ScenePrimaryMetricFilter,
): boolean {
  const keys = new Set(config.indicators.map(normalizeMetricKey));
  return keys.has(normalizeMetricKey(evidence.field_group))
    || keys.has(normalizeMetricKey(evidence.indicator_name || ""));
}

function numericMetricValue(text: string): number | null {
  const values: number[] = [];
  const scaledPattern = /(\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(billion|bn|million|mn|thousand|k)\b/gi;
  for (const match of text.matchAll(scaledPattern)) {
    values.push(parseMetricNumber(match[1]) * metricMultiplier(match[2]));
  }

  const numberPattern = /\d+(?:,\d{3})*(?:\.\d+)?/g;
  for (const match of text.matchAll(numberPattern)) {
    values.push(parseMetricNumber(match[0]));
  }

  const finiteValues = values.filter((value) => Number.isFinite(value) && value > 0);
  if (finiteValues.length === 0) {
    return null;
  }
  const nonYearValues = finiteValues.filter((value) => value < 1900 || value > 2035);
  return Math.max(...(nonYearValues.length > 0 ? nonYearValues : finiteValues));
}

function parseMetricNumber(value: string): number {
  return Number(value.replaceAll(",", ""));
}

function metricMultiplier(unit: string): number {
  const normalized = unit.toLowerCase();
  if (normalized === "billion" || normalized === "bn") {
    return 1_000_000_000;
  }
  if (normalized === "million" || normalized === "mn") {
    return 1_000_000;
  }
  if (normalized === "thousand" || normalized === "k") {
    return 1_000;
  }
  return 1;
}

function primaryMetricFilterLabel(scene: string): string {
  return SCENE_PRIMARY_METRIC_FILTERS[scene]?.label || "Primary metric";
}

function normalizeMetricKey(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function compareMarkers(a: GlobeMarker, b: GlobeMarker): number {
  if (b.candidateCount !== a.candidateCount) {
    return b.candidateCount - a.candidateCount;
  }
  return `${a.country} ${a.label}`.localeCompare(`${b.country} ${b.label}`);
}

function countryKey(country: string): string {
  return `country::${country.trim().toLowerCase()}`;
}

function cityKey(country: string, city: string): string {
  return `${country.trim().toLowerCase()}::${city.trim().toLowerCase()}`;
}

function cityFocus(marker: CityMarker): { lat: number; lng: number } {
  return { lat: marker.lat, lng: marker.lng };
}

function globeMarkerLabel(marker: DisplayGlobeMarker): string {
  const topScenes = Object.entries(marker.scenes)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([scene, count]) => `${sceneLabel(scene)} ${count}`)
    .join(" · ");
  if (marker.kind === "city_cluster") {
    return [
      `${formatNumber(marker.cityCount)} cities in ${marker.country}`,
      `${formatNumber(marker.candidateCount)} candidates`,
      `${formatNumber(marker.reviewCount)} review`,
      `${formatNumber(marker.sourceCount)} sources`,
      "Click to expand city markers",
      topScenes,
    ].filter(Boolean).join("\n");
  }
  const mappedText = marker.kind === "country"
    ? marker.positionSource === "display_anchor"
      ? "country display anchor"
      : `${formatNumber(marker.mappedCityCount)} mapped cities`
    : marker.positionSource === "city_geocode"
      ? "estimated city centroid"
      : marker.positionSource === "property_average"
        ? "estimated city position"
      : `${formatNumber(marker.mapReadyCount)} mapped`;
  return [
    marker.kind === "country" ? marker.country : `${marker.city}, ${marker.country}`,
    `${formatNumber(marker.candidateCount)} candidates`,
    mappedText,
    `${formatNumber(marker.reviewCount)} review`,
    topScenes,
  ].filter(Boolean).join("\n");
}

function globeMarkerAriaLabel(marker: DisplayGlobeMarker): string {
  if (marker.kind === "country") {
    const sourceText = marker.positionSource === "display_anchor" ? ", country display marker" : "";
    return `${marker.country}, ${formatNumber(marker.candidateCount)} candidate properties${sourceText}`;
  }
  if (marker.kind === "city_cluster") {
    return `${formatNumber(marker.cityCount)} clustered cities in ${marker.country}, ${formatNumber(marker.candidateCount)} candidate properties, click to expand`;
  }
  return `${marker.city}, ${marker.country}, ${formatNumber(marker.candidateCount)} candidate properties`;
}

function GlobeMarkerButton({
  marker,
  active,
  onSelect,
  spiderChild,
  style,
}: {
  marker: DisplayGlobeMarker;
  active: boolean;
  onSelect: (marker: DisplayGlobeMarker) => void;
  spiderChild?: boolean;
  style?: React.CSSProperties;
}) {
  const markerClass = marker.kind === "city_cluster" ? "cluster-marker" : `${marker.kind}-marker`;
  const sourceClass = marker.kind === "country" && marker.positionSource === "display_anchor"
    ? "micro-country-marker"
    : "";
  return (
    <button
      className={`city-marker globe-marker ${markerClass} ${sourceClass} ${active ? "active" : ""} ${spiderChild ? "spider-child" : ""}`}
      type="button"
      aria-label={globeMarkerAriaLabel(marker)}
      title={globeMarkerLabel(marker)}
      style={style}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(marker);
      }}
    >
      <span className="city-marker-ring" aria-hidden="true" />
      <span className="city-marker-core">{formatNumber(marker.candidateCount)}</span>
      <span className="city-marker-label">
        {marker.kind === "city_cluster" ? `${formatNumber(marker.cityCount)} cities` : marker.label}
      </span>
    </button>
  );
}

function projectGlobeMarkers(
  globe: GlobeProjectionMethods,
  markers: GlobeMarker[],
  selectedCityKey: string,
  pointOfViewOverride?: { lat: number; lng: number } | null,
): ScreenGlobeMarker[] {
  const pov = pointOfViewOverride || globe.pointOfView();
  return markers.map((marker) => {
    const isActiveCity = marker.kind === "city" && marker.key === selectedCityKey;
    const altitude = marker.kind === "country" ? 0.065 : isActiveCity ? 0.07 : 0.045;
    const coords = globe.getScreenCoords(marker.lat, marker.lng, altitude);
    return {
      ...marker,
      x: Math.round(coords.x * 10) / 10,
      y: Math.round(coords.y * 10) / 10,
      visible: Number.isFinite(coords.x)
        && Number.isFinite(coords.y)
        && globeMarkerFacesCamera(marker, pov),
    };
  });
}

function projectMockGlobeMarkers(
  markers: GlobeMarker[],
  selectedCityKey: string,
  stageSize: { width: number; height: number },
  pointOfViewOverride?: { lat: number; lng: number } | null,
): ScreenGlobeMarker[] {
  const centerLat = average(markers.map((marker) => marker.lat));
  const centerLng = average(markers.map((marker) => marker.lng));
  const cityScale = 42;
  const countrySpacing = Math.min(170, Math.max(96, stageSize.width / Math.max(3, markers.length + 1)));

  return markers.map((marker, index) => {
    const isActiveCity = marker.kind === "city" && marker.key === selectedCityKey;
    const x = marker.kind === "city"
      ? stageSize.width / 2 + (marker.lng - centerLng) * cityScale
      : stageSize.width / 2 + (index - (markers.length - 1) / 2) * countrySpacing;
    const y = marker.kind === "city"
      ? stageSize.height / 2 - (marker.lat - centerLat) * cityScale
      : stageSize.height * 0.45;
    return {
      ...marker,
      x: roundScreenCoordinate(x),
      y: roundScreenCoordinate(y),
      visible: pointOfViewOverride ? globeMarkerFacesCamera(marker, pointOfViewOverride) : true,
      spiderChild: false,
      ...(isActiveCity ? { y: roundScreenCoordinate(y - 8) } : {}),
    };
  });
}

function clusterScreenGlobeMarkers(
  projected: ScreenGlobeMarker[],
  {
    expandedClusterKey,
    selectedCityKey,
    selectedCountry,
    stageSize,
  }: {
    expandedClusterKey: string;
    selectedCityKey: string;
    selectedCountry: string;
    stageSize: { width: number; height: number };
  },
): ScreenDisplayGlobeMarker[] {
  if (!selectedCountry) {
    return projected;
  }

  const passthrough: ScreenDisplayGlobeMarker[] = [];
  const clusterableCities: ScreenGlobeMarker[] = [];
  projected.forEach((marker) => {
    if (marker.kind !== "city" || !marker.visible || marker.key === selectedCityKey) {
      passthrough.push(marker);
      return;
    }
    clusterableCities.push(marker);
  });

  const clustered: ScreenDisplayGlobeMarker[] = [];
  buildScreenCityClusters(clusterableCities).forEach((cluster) => {
    if (cluster.length === 1) {
      clustered.push(cluster[0]);
      return;
    }
    const clusterMarker = makeCityClusterMarker(cluster);
    if (clusterMarker.key === expandedClusterKey) {
      clustered.push(...spiderCityCluster(cluster, clusterMarker, stageSize));
      return;
    }
    clustered.push(clusterMarker);
  });

  return [...passthrough, ...clustered];
}

function buildScreenCityClusters(markers: ScreenGlobeMarker[]): ScreenGlobeMarker[][] {
  const clusters: ScreenGlobeMarker[][] = [];
  markers.forEach((marker) => {
    let closestClusterIndex = -1;
    let closestDistance = Number.POSITIVE_INFINITY;
    clusters.forEach((cluster, index) => {
      const center = screenClusterCenter(cluster);
      const distance = screenDistance(marker, center);
      if (distance <= CITY_CLUSTER_RADIUS_PX && distance < closestDistance) {
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

function makeCityClusterMarker(cluster: ScreenGlobeMarker[]): ScreenCityClusterMarker {
  const center = screenClusterCenter(cluster);
  const cityKeys = cluster.map((marker) => marker.key).sort();
  const scenes: Record<string, number> = {};
  cluster.forEach((marker) => {
    Object.entries(marker.scenes).forEach(([scene, count]) => {
      scenes[scene] = (scenes[scene] || 0) + count;
    });
  });

  return {
    kind: "city_cluster",
    key: `city_cluster::${cityKeys.join("|")}`,
    country: cluster[0]?.country || "",
    label: `${formatNumber(cluster.length)} cities`,
    lat: average(cluster.map((marker) => marker.lat)),
    lng: average(cluster.map((marker) => marker.lng)),
    cityCount: cluster.length,
    candidateCount: cluster.reduce((total, marker) => total + marker.candidateCount, 0),
    mapReadyCount: cluster.reduce((total, marker) => total + marker.mapReadyCount, 0),
    reviewCount: cluster.reduce((total, marker) => total + marker.reviewCount, 0),
    sourceCount: cluster.reduce((total, marker) => total + marker.sourceCount, 0),
    scenes,
    propertyIds: cluster.flatMap((marker) => marker.propertyIds),
    cityKeys,
    x: center.x,
    y: center.y,
    visible: true,
  };
}

function spiderCityCluster(
  cluster: ScreenGlobeMarker[],
  clusterMarker: ScreenCityClusterMarker,
  stageSize: { width: number; height: number },
): ScreenGlobeMarker[] {
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

function screenClusterCenter(cluster: ScreenGlobeMarker[]): { x: number; y: number } {
  return {
    x: roundScreenCoordinate(average(cluster.map((marker) => marker.x))),
    y: roundScreenCoordinate(average(cluster.map((marker) => marker.y))),
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

function sameScreenGlobeMarkers(
  current: ScreenDisplayGlobeMarker[],
  next: ScreenDisplayGlobeMarker[],
): boolean {
  return current.length === next.length && current.every((marker, index) => {
    const nextMarker = next[index];
    return Boolean(nextMarker)
      && marker.key === nextMarker.key
      && marker.kind === nextMarker.kind
      && marker.x === nextMarker.x
      && marker.y === nextMarker.y
      && marker.visible === nextMarker.visible
      && marker.candidateCount === nextMarker.candidateCount
      && marker.spiderChild === nextMarker.spiderChild
      && markerCityCount(marker) === markerCityCount(nextMarker);
  });
}

function markerCityCount(marker: ScreenDisplayGlobeMarker): number {
  return marker.kind === "city_cluster" ? marker.cityCount : 1;
}

function globeMarkerFacesCamera(marker: GlobeMarker, pov: { lat: number; lng: number }): boolean {
  return angularDistanceDegrees(marker.lat, marker.lng, pov.lat, pov.lng) <= 98;
}

function angularDistanceDegrees(latA: number, lngA: number, latB: number, lngB: number): number {
  const phiA = toRadians(latA);
  const phiB = toRadians(latB);
  const deltaLng = toRadians(normalizeLongitudeDelta(lngB - lngA));
  const cosAngle =
    Math.sin(phiA) * Math.sin(phiB)
    + Math.cos(phiA) * Math.cos(phiB) * Math.cos(deltaLng);
  return toDegrees(Math.acos(Math.min(1, Math.max(-1, cosAngle))));
}

function normalizeLongitudeDelta(delta: number): number {
  return ((delta + 540) % 360) - 180;
}

function toRadians(value: number): number {
  return value * Math.PI / 180;
}

function toDegrees(value: number): number {
  return value * 180 / Math.PI;
}

function summaryFromPackets(
  packets: SitePacket[],
  marker: CityMarker | null,
): CountrySummary {
  if (packets.length === 0 && marker) {
    return summaryFromCityMarker(marker);
  }
  return packets.reduce<CountrySummary>(
    (total, packet) => {
      total.candidate_count += 1;
      total.map_point_count += 1;
      total.review_count += packet.review_queue.length;
      total.source_count += new Set(packet.evidence.map((item) => item.source_url)).size;
      total.scenes[packet.entity.scene_type] = (total.scenes[packet.entity.scene_type] || 0) + 1;
      return total;
    },
    {
      country: marker?.country || "",
      candidate_count: 0,
      map_point_count: 0,
      coordinate_review_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    },
  );
}

function summaryFromCityMarker(marker: CityMarker): CountrySummary {
  return {
    country: marker.country,
    candidate_count: marker.candidateCount,
    map_point_count: marker.mapReadyCount,
    coordinate_review_count: Math.max(0, marker.candidateCount - marker.mapReadyCount),
    review_count: marker.reviewCount,
    source_count: marker.sourceCount,
    scenes: { ...marker.scenes },
  };
}

function aggregateSummary(summaries: CountrySummary[], selectedCountry: string): CountrySummary {
  const target = summaries.find((summary) => summary.country === selectedCountry);
  if (target) {
    return target;
  }
  if (selectedCountry) {
    return {
      country: selectedCountry,
      candidate_count: 0,
      map_point_count: 0,
      coordinate_review_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    };
  }
  return summaries.reduce<CountrySummary>(
    (total, row) => {
      total.candidate_count += row.candidate_count;
      total.map_point_count += row.map_point_count;
      total.coordinate_review_count += row.coordinate_review_count;
      total.review_count += row.review_count;
      total.source_count += row.source_count;
      Object.entries(row.scenes).forEach(([scene, count]) => {
        total.scenes[scene] = (total.scenes[scene] || 0) + count;
      });
      return total;
    },
    {
      country: selectedCountry,
      candidate_count: 0,
      map_point_count: 0,
      coordinate_review_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    },
  );
}

function aggregateRegionSummaries(summaries: CountrySummary[]): RegionSummary[] {
  const regions = new Map<string, RegionSummary>();
  summaries.forEach((summary) => {
    const region = regionForCountry(summary.country);
    const total = regions.get(region) || {
      region,
      country_count: 0,
      candidate_count: 0,
      map_point_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
    };
    total.country_count += 1;
    total.candidate_count += summary.candidate_count;
    total.map_point_count += summary.map_point_count;
    total.review_count += summary.review_count;
    total.source_count += summary.source_count;
    Object.entries(summary.scenes).forEach(([scene, count]) => {
      total.scenes[scene] = (total.scenes[scene] || 0) + count;
    });
    regions.set(region, total);
  });
  return Array.from(regions.values()).sort(
    (left, right) =>
      right.candidate_count - left.candidate_count
      || regionSortIndex(left.region) - regionSortIndex(right.region)
      || left.region.localeCompare(right.region),
  );
}

function regionForCountry(country: string): string {
  return REGION_BY_COUNTRY.get(normalizeCountryName(country)) || "Other regions";
}

function normalizeCountryName(country: string): string {
  return country
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/&/g, "and")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function regionSortIndex(region: string): number {
  const index = TARGET_REGION_ORDER.indexOf(region as TargetRegion);
  return index === -1 ? TARGET_REGION_ORDER.length : index;
}

function countryFocus(
  country: string,
  features: MapFeature[],
  polygons: CountryFeature[],
): { lat: number; lng: number } {
  const countryFeatures = features.filter((item) => item.properties.country === country);
  if (countryFeatures.length > 0) {
    return propertyFocus(countryFeatures);
  }
  const position = countryDisplayPosition(country, polygons);
  return { lat: position.lat, lng: position.lng };
}

function countryDisplayPosition(
  country: string,
  polygons: CountryFeature[],
): { lat: number; lng: number; positionSource: CountryPositionSource } {
  return countryDisplayPositionFromPolygon(
    country,
    polygons.find((item) => countryName(item) === country),
  );
}

function buildCountryDisplayPositionCache(
  polygons: CountryFeature[],
): Map<string, { lat: number; lng: number; positionSource: CountryPositionSource }> {
  const cache = new Map<string, { lat: number; lng: number; positionSource: CountryPositionSource }>();
  polygons.forEach((polygon) => {
    const name = countryName(polygon);
    if (name) {
      cache.set(name, countryDisplayPositionFromPolygon(name, polygon));
    }
  });
  return cache;
}

function countryDisplayPositionFromPolygon(
  country: string,
  polygon: CountryFeature | undefined,
): { lat: number; lng: number; positionSource: CountryPositionSource } {
  const anchor = COUNTRY_DISPLAY_ANCHORS[normalizeCountryName(country)];
  if (!polygon) {
    return defaultCountryDisplayPosition(country);
  }
  const points = polygonPoints(polygon.geometry);
  if (points.length === 0) {
    return defaultCountryDisplayPosition(country);
  }
  if (anchor && isTinyCountryPolygon(points)) {
    return { ...anchor, positionSource: "display_anchor" };
  }
  const totals = points.reduce(
    (sum, [lng, lat]) => ({ lng: sum.lng + lng, lat: sum.lat + lat }),
    { lng: 0, lat: 0 },
  );
  return {
    lng: totals.lng / points.length,
    lat: totals.lat / points.length,
    positionSource: "polygon_centroid",
  };
}

function defaultCountryDisplayPosition(
  country: string,
): { lat: number; lng: number; positionSource: CountryPositionSource } {
  const anchor = COUNTRY_DISPLAY_ANCHORS[normalizeCountryName(country)];
  return anchor
    ? { ...anchor, positionSource: "display_anchor" }
    : { lat: 20, lng: 12, positionSource: "default_fallback" };
}

function propertyFocus(features: MapFeature[]): { lat: number; lng: number } {
  if (features.length === 0) {
    return { lat: 28, lng: 15 };
  }
  const totals = features.reduce(
    (sum, item) => ({
      lng: sum.lng + item.geometry.coordinates[0],
      lat: sum.lat + item.geometry.coordinates[1],
    }),
    { lng: 0, lat: 0 },
  );
  return { lng: totals.lng / features.length, lat: totals.lat / features.length };
}

function isTinyCountryPolygon(points: [number, number][]): boolean {
  const bounds = points.reduce(
    (total, [lng, lat]) => ({
      minLat: Math.min(total.minLat, lat),
      maxLat: Math.max(total.maxLat, lat),
      minLng: Math.min(total.minLng, lng),
      maxLng: Math.max(total.maxLng, lng),
    }),
    {
      minLat: Number.POSITIVE_INFINITY,
      maxLat: Number.NEGATIVE_INFINITY,
      minLng: Number.POSITIVE_INFINITY,
      maxLng: Number.NEGATIVE_INFINITY,
    },
  );
  return Math.max(
    bounds.maxLat - bounds.minLat,
    bounds.maxLng - bounds.minLng,
  ) < TINY_COUNTRY_POLYGON_SPAN_DEGREES;
}

function polygonPoints(geometry: Geometry): [number, number][] {
  if (geometry.type === "Polygon") {
    return geometry.coordinates.flat() as [number, number][];
  }
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.flat(2) as [number, number][];
  }
  return [];
}

function addParam(params: URLSearchParams, key: string, value: string) {
  if (value) {
    params.set(key, value);
  }
}

function exportFilterPayload({
  selectedCountry,
  selectedCityMarker,
  sceneFilter,
  evidenceFilter,
  actionFilter,
  reviewOnly,
}: {
  selectedCountry: string;
  selectedCityMarker: CityMarker | null;
  sceneFilter: string;
  evidenceFilter: string;
  actionFilter: string;
  reviewOnly: boolean;
}): Record<string, string | boolean> {
  const payload: Record<string, string | boolean> = {};
  addPayloadParam(payload, "country", selectedCityMarker?.country || selectedCountry);
  addPayloadParam(payload, "city", selectedCityMarker?.city || "");
  addPayloadParam(payload, "scene_type", sceneFilter);
  addPayloadParam(payload, "evidence_status", evidenceFilter);
  addPayloadParam(payload, "action_class", actionFilter);
  if (reviewOnly) {
    payload.has_review_issue = true;
  }
  return payload;
}

function addPayloadParam(payload: Record<string, string | boolean>, key: string, value: string) {
  if (value) {
    payload[key] = value;
  }
}

function withQuery(path: string, query: string): string {
  return query ? `${path}?${query}` : path;
}

function sceneLabel(scene: string): string {
  return SCENE_LABELS[scene] || scene.replaceAll("_", " ");
}

function taskBacklogTotal(backlog: Record<string, number>): number {
  return Object.values(backlog).reduce((total, count) => total + count, 0);
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "Unknown";
  }
  return new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Request failed";
}

declare global {
  interface Window {
    __isite2SelectCountry?: (country: string) => void;
    __isite2GlobeControlState?: () => GlobeControlState | null;
    __isite2SetPointOfView?: (lat: number, lng: number, altitude?: number) => void;
  }
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
