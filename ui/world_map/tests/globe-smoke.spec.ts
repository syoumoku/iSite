import { expect, test, type Page } from "@playwright/test";

const RUN_ID = "mock-run-001";
const SECOND_RUN_ID = "mock-run-002";
const SCENES = ["airport_terminal", "convention_center", "stadium", "mall_mixed_use"];
const COUNTRIES = [
  { country: "Algeria" },
  { country: "Egypt" },
];
const PLACES = [
  { country: "Algeria", city: "Algiers", lat: 36.691, lng: 3.215 },
  { country: "Algeria", city: "Oran", lat: 35.697, lng: -0.633 },
  { country: "Egypt", city: "Cairo", lat: 30.112, lng: 31.4 },
];
const APAC_PLACES = [
  { country: "Sri Lanka", city: "Colombo", lat: 6.9271, lng: 79.8612 },
  { country: "Cambodia", city: "Phnom Penh", lat: 11.5564, lng: 104.9282 },
  { country: "Maldives", city: "Male", lat: 4.1755, lng: 73.5093 },
];
const CITY_ONLY_PLACE = { country: "Algeria", city: "Setif", lat: 36.1911, lng: 5.4137 };
const DENSE_ALGERIA_PLACES = [
  { country: "Algeria", city: "Bab Ezzouar", lat: 36.7147, lng: 3.1838 },
  { country: "Algeria", city: "Ben Aknoun", lat: 36.7582, lng: 3.0135 },
  { country: "Algeria", city: "Bir Mourad Rais", lat: 36.7351, lng: 3.0506 },
  { country: "Algeria", city: "Dar El Beida", lat: 36.7133, lng: 3.2125 },
  { country: "Algeria", city: "El Harrach", lat: 36.7161, lng: 3.1366 },
  { country: "Algeria", city: "El Madania", lat: 36.7473, lng: 3.0702 },
  { country: "Algeria", city: "Hydra", lat: 36.7476, lng: 3.0404 },
  { country: "Algeria", city: "Hussein Dey", lat: 36.7452, lng: 3.0982 },
  { country: "Algeria", city: "Kouba", lat: 36.7275, lng: 3.0851 },
  { country: "Algeria", city: "Mohammadia", lat: 36.7357, lng: 3.1469 },
];
const EMPTY_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lS8tJAAAAABJRU5ErkJggg==",
  "base64",
);

type MockHeroImage = {
  url: string;
  alt_text: string;
  source_name: string;
  source_url: string;
  source_date?: string;
  license?: string;
};
const PACKETS = COUNTRIES.flatMap((country) =>
  PLACES.filter((place) => place.country === country.country).flatMap((place) =>
    SCENES.map((scene, index) =>
      createPacket(place, scene, index, {
        coordinateStatus:
          place.country === "Algeria" && place.city === "Algiers" && scene === "stadium"
            ? "Review Required"
            : "Map Ready",
      }),
    ),
  ),
).concat(createPacket(CITY_ONLY_PLACE, "airport_terminal", 0, { coordinateStatus: "Review Required" }));
const DENSE_PACKETS = PACKETS.concat(
  DENSE_ALGERIA_PLACES.map((place, index) => createPacket(place, SCENES[index % SCENES.length], 0)),
);
const DENSE_ALGIERS_PROPERTY_PACKETS = Array.from({ length: 12 }, (_, index) => {
  const row = Math.floor(index / 4);
  const column = index % 4;
  const packet = createPacket(
    {
      country: "Algeria",
      city: "Algiers",
      lat: 36.723 + row * 0.004,
      lng: 3.044 + column * 0.004,
    },
    SCENES[index % SCENES.length],
    index,
  );
  packet.entity.property_id = `algiers-dense-property-${index + 1}`;
  packet.entity.property_name = `Algiers dense property ${index + 1}`;
  return packet;
});
const LARGE_ALGERIA_CARD_PACKETS = Array.from({ length: 80 }, (_, index) => {
  const row = Math.floor(index / 8);
  const column = index % 8;
  const packet = createPacket(
    {
      country: "Algeria",
      city: index % 2 === 0 ? "Algiers" : "Oran",
      lat: 36.6 + row * 0.01,
      lng: 2.8 + column * 0.01,
    },
    SCENES[index % SCENES.length],
    index,
    {
      heroImage: {
        url: `https://images.example.test/large-country-card-${index + 1}.jpg`,
        alt_text: `Large Algeria property ${index + 1}`,
        source_name: "Mock Image Archive",
        source_url: `https://example.org/large-country-card-${index + 1}`,
        source_date: "2026-05-26",
      },
    },
  );
  packet.entity.property_id = `large-algeria-card-${index + 1}`;
  packet.entity.property_name = `Large Algeria card property ${index + 1}`;
  return packet;
});
const APAC_PACKETS = APAC_PLACES.map((place, index) =>
  createPacket(place, SCENES[index % SCENES.length], 0),
);
const EUROPE_PACKETS = [
  createPacket({ country: "Albania", city: "Tirana", lat: 41.3275, lng: 19.8189 }, "airport_terminal", 0),
  createPacket({ country: "Bosnia and Herzegovina", city: "Sarajevo", lat: 43.8563, lng: 18.4131 }, "stadium", 0),
  createPacket({ country: "Bulgaria", city: "Sofia", lat: 42.6977, lng: 23.3219 }, "mall_mixed_use", 0),
  createPacket({ country: "Croatia", city: "Zagreb", lat: 45.815, lng: 15.9819 }, "convention_center", 0),
  createPacket({ country: "Cyprus", city: "Nicosia", lat: 35.1856, lng: 33.3823 }, "luxury_hotel_mice", 0),
  createPacket({ country: "Germany", city: "Berlin", lat: 52.52, lng: 13.405 }, "airport_terminal", 0),
  createPacket({ country: "Greece", city: "Athens", lat: 37.9838, lng: 23.7275 }, "stadium", 0),
  createPacket({ country: "Czech Republic", city: "Prague", lat: 50.0755, lng: 14.4378 }, "mall_mixed_use", 0),
  createPacket({ country: "France", city: "Paris", lat: 48.8566, lng: 2.3522 }, "convention_center", 0),
  createPacket({ country: "Moldova", city: "Chisinau", lat: 47.0105, lng: 28.8638 }, "airport_terminal", 0),
  createPacket({ country: "Montenegro", city: "Podgorica", lat: 42.4304, lng: 19.2594 }, "stadium", 0),
  createPacket({ country: "North Macedonia", city: "Skopje", lat: 41.9981, lng: 21.4254 }, "mall_mixed_use", 0),
  createPacket({ country: "Serbia", city: "Belgrade", lat: 44.8125, lng: 20.4612 }, "convention_center", 0),
  createPacket({ country: "Slovakia", city: "Bratislava", lat: 48.1486, lng: 17.1077 }, "airport_terminal", 0),
  createPacket({ country: "Slovenia", city: "Ljubljana", lat: 46.0569, lng: 14.5058 }, "stadium", 0),
  createPacket({ country: "Switzerland", city: "Zurich", lat: 47.3769, lng: 8.5417 }, "luxury_hotel_mice", 0),
];
const REUNION_PACKET = createPacket(
  { country: "Reunion", city: "Saint-Denis", lat: -20.8789, lng: 55.4481 },
  "airport_terminal",
  0,
);
const RUSSIA_PACKET = createPacket(
  { country: "Russia", city: "Moscow", lat: 55.7558, lng: 37.6173 },
  "airport_terminal",
  0,
);
const UNMAPPED_REGION_PACKETS = Array.from({ length: 5 }, (_, index) =>
  createPacket(
    { country: "Unmapped Country", city: `Unmapped City ${index + 1}`, lat: 44 + index * 0.1, lng: 8 + index * 0.1 },
    SCENES[index % SCENES.length],
    index,
  ),
);
const FALLBACK_RISK_COUNTRY_PACKETS = [
  createPacket({ country: "Barbados", city: "Bridgetown", lat: 13.0975, lng: -59.6167 }, "airport_terminal", 0),
  createPacket({ country: "Central African Republic", city: "Bangui", lat: 4.3947, lng: 18.5582 }, "airport_terminal", 0),
  createPacket({ country: "Cote d'Ivoire", city: "Abidjan", lat: 5.36, lng: -4.0083 }, "mall_mixed_use", 0),
  createPacket({ country: "Czech Republic", city: "Prague", lat: 50.0755, lng: 14.4378 }, "mall_mixed_use", 0),
  createPacket({ country: "Democratic Republic of the Congo", city: "Kinshasa", lat: -4.4419, lng: 15.2663 }, "stadium", 0),
  createPacket({ country: "Dominican Republic", city: "Santo Domingo", lat: 18.4861, lng: -69.9312 }, "airport_terminal", 0),
];
const AFRICAN_ISLAND_PACKETS = [
  createPacket({ country: "Cape Verde", city: "Praia", lat: 14.9177, lng: -23.5092 }, "airport_terminal", 0),
  createPacket({ country: "Sao Tome and Principe", city: "Sao Tome", lat: 0.3365, lng: 6.7273 }, "airport_terminal", 0),
];
const NORTH_AFRICA_PACKETS = [
  createPacket({ country: "Algeria", city: "Algiers", lat: 36.7538, lng: 3.0588 }, "airport_terminal", 0),
  createPacket({ country: "Egypt", city: "Cairo", lat: 30.0444, lng: 31.2357 }, "stadium", 0),
  createPacket({ country: "Libya", city: "Tripoli", lat: 32.8872, lng: 13.1913 }, "mall_mixed_use", 0),
  createPacket({ country: "Morocco", city: "Casablanca", lat: 33.5731, lng: -7.5898 }, "convention_center", 0),
  createPacket({ country: "Tunisia", city: "Tunis", lat: 36.8065, lng: 10.1815 }, "airport_terminal", 0),
];
const MIDDLE_EAST_CENTRAL_ASIA_PACKETS = [
  createPacket({ country: "Saudi Arabia", city: "Riyadh", lat: 24.7136, lng: 46.6753 }, "airport_terminal", 0),
  createPacket({ country: "Kazakhstan", city: "Astana", lat: 51.1694, lng: 71.4491 }, "convention_center", 0),
  createPacket({ country: "United Arab Emirates", city: "Dubai", lat: 25.2048, lng: 55.2708 }, "mall_mixed_use", 0),
];
const MANY_COUNTRY_SCOPE_PACKETS = Array.from({ length: 80 }, (_, index) =>
  createPacket(
    {
      country: `Country ${String(index + 1).padStart(2, "0")}`,
      city: `City ${index + 1}`,
      lat: -48 + (index % 24) * 4,
      lng: -150 + (index % 40) * 7,
    },
    SCENES[index % SCENES.length],
    index,
  ),
);

test("renders the opportunity globe as the default product surface", async ({ page }) => {
  const dataRequests: string[] = [];
  const scanRunRequests: string[] = [];
  await installMockApi(page, { dataRequests, scanRunRequests });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "overview");
  await expect(page.locator(".globe-stage")).toBeVisible();
  await expect(page.locator(".satellite-navigator")).toHaveCount(0);
  await expect(page.locator(".agent-workbench-stage")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Agent workbench view" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Globe view" })).toHaveCount(0);
  await expectGlobeMarkerCount(page, 2);
  await expect(page.locator(".insight-panel")).toBeVisible();
  expect(scanRunRequests).toEqual([]);
});

test("shows AI Thinking during delayed overview bootstrap and defers discovery status", async ({ page }) => {
  const dataRequests: string[] = [];
  const discoveryRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    discoveryRequests,
    countrySummaryDelayMs: 900,
    discoveryDelayMs: 3_000,
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("status")).toContainText("AI Thinking");
  await expect(page.getByRole("status")).toContainText("Loading country intelligence");
  expect(discoveryRequests).toEqual([]);

  await expectGlobeMarkerCount(page, 2, 10_000);
  await expect(page.getByLabel("Region distribution")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "AI Thinking" })).toHaveCount(0);
  await expect.poll(() => requestCount(dataRequests, "/map/country-summary")).toBe(1);
  await expect.poll(() => discoveryRequests.length, { timeout: 5_000 }).toBeGreaterThan(0);
});

test("keeps property cards available when city summary loading fails", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    citySummaryFailureCount: 1,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => window.__isite2SelectCountry?.("Algeria"));

  await expectPropertyCardCount(
    page,
    PACKETS.filter((packet) => packet.entity.country === "Algeria").length,
  );
  await expect(page.getByText("No visible points", { exact: true })).toHaveCount(0);
  expect(requestCount(dataRequests, "/map/city-summary")).toBe(1);
  expect(requestCount(dataRequests, "/properties")).toBe(1);
});

test("shows a retry state instead of a false empty state after a property request failure", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    propertyFailureCount: 1,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => window.__isite2SelectCountry?.("Algeria"));

  const loadFailure = page.locator(".empty-panel[role='alert']");
  await expect(loadFailure).toContainText("Property data could not be loaded.");
  await expect(page.getByText("No visible points", { exact: true })).toHaveCount(0);
  const retry = loadFailure.getByRole("button", { name: "Retry", exact: true });
  await expect(retry).toHaveCount(1);
  await retry.click();

  await expectPropertyCardCount(
    page,
    PACKETS.filter((packet) => packet.entity.country === "Algeria").length,
  );
  await expect(loadFailure).toHaveCount(0);
  expect(requestCount(dataRequests, "/properties")).toBe(2);
});

test("highlights data countries without persistent beacon rings or animated paths", async ({ page }) => {
  await installMockApi(page, { dataRequests: [] });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 2);

  const visualState = await page.evaluate(() => window.__isite2CountryVisualState?.());
  expect(visualState?.boundaryPathCount).toBe(0);
  expect(visualState?.beaconRingCount).toBe(0);
  expect(visualState?.beaconMotionEnabled).toBe(false);
  expect(visualState?.beaconRepeatPeriods).toEqual([]);
  expect(visualState?.dataCountryCapColor).not.toBe("rgba(32, 206, 177, 0.28)");
  expect(visualState?.dataCountryCapColor).not.toContain("32, 206, 177");
  expect(visualState?.dataCountryStrokeColor).toContain("238, 252, 249");
  expect(visualState?.pathDashAnimateTime).toBe(0);
});

test("keeps country marker motion disabled by default", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installMockApi(page, { dataRequests: [] });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 2);

  const visualState = await page.evaluate(() => window.__isite2CountryVisualState?.());
  expect(visualState?.boundaryPathCount).toBe(0);
  expect(visualState?.beaconRingCount).toBe(0);
  expect(visualState?.beaconMotionEnabled).toBe(false);
  expect(visualState?.pathDashAnimateTime).toBe(0);
});

test("animates only the hovered country marker", async ({ page }) => {
  await installMockApi(page, { dataRequests: [] });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 2);

  const defaultAnimation = await page.evaluate(() => {
    const ring = document.querySelector<HTMLElement>(".country-marker .city-marker-ring");
    return ring ? getComputedStyle(ring).animationName : "";
  });
  expect(defaultAnimation).toBe("none");
  const defaultLabelOpacity = await page.evaluate(() => {
    const label = document.querySelector<HTMLElement>(".country-marker .city-marker-label");
    return label ? Number(getComputedStyle(label).opacity) : 0;
  });
  expect(defaultLabelOpacity).toBeGreaterThan(0.8);
  await expect(page.locator(".country-marker").first()).not.toHaveAttribute("title", /.+/);

  await page.getByRole("button", { name: /Algeria, 9 candidate properties/ }).hover();
  await expect(page.getByRole("button", { name: /Algeria, 9 candidate properties/ })).toHaveClass(/hovered/);
  await expect(page.locator(".map-hover-tooltip")).toContainText("Algeria");
  await expect(page.locator(".map-hover-tooltip")).toContainText("9 candidates");
  const hoveredAnimation = await page.evaluate(() => {
    const marker = Array.from(document.querySelectorAll<HTMLElement>(".country-marker"))
      .find((item) => item.classList.contains("hovered"));
    const ring = marker?.querySelector<HTMLElement>(".city-marker-ring");
    return ring ? getComputedStyle(ring).animationName : "";
  });
  expect(hoveredAnimation).toBe("country-beacon-pulse");

  const nonHoveredAnimations = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>(".country-marker:not(.hovered) .city-marker-ring"))
      .map((ring) => getComputedStyle(ring).animationName),
  );
  expect(nonHoveredAnimations.every((animation) => animation === "none")).toBe(true);
});

test("filters overview KPIs scene distribution and globe markers by clicked region", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: PACKETS.concat(APAC_PACKETS),
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 5);
  await expect(page.locator(".kpi").nth(0)).toContainText("5");
  await expect(page.locator(".kpi").nth(1)).toContainText("16");
  await expect(page.getByLabel("Scene distribution").locator(".scene-row", { hasText: "Airport" }))
    .toContainText("5");

  const africaCard = page.getByRole("button", { name: /Africa/ });
  await africaCard.click();
  await expect(africaCard).toHaveClass(/active/);
  await expect(africaCard).not.toHaveAttribute("aria-pressed", /.+/);
  await expect(page.getByRole("button", { name: "Back to all" })).toBeVisible();
  await expectGlobeMarkerCount(page, 2);
  await expect(page.locator(".kpi").nth(0)).toContainText("2");
  await expect(page.locator(".kpi").nth(1)).toContainText("13");
  await expect(page.getByLabel("Scene distribution").locator(".scene-row", { hasText: "Airport" }))
    .toContainText("4");
  const regionControlState = await page.evaluate(() => window.__isite2GlobeControlState?.());
  expect(regionControlState?.autoRotate).toBe(false);

  await africaCard.click();
  await expect(africaCard).not.toHaveClass(/active/);
  await expect(page.getByRole("button", { name: "Back to all" })).toHaveCount(0);
  await expectGlobeMarkerCount(page, 5);
  await expect(page.locator(".kpi").nth(0)).toContainText("5");
  await expect(page.locator(".kpi").nth(1)).toContainText("16");
  const globalControlState = await page.evaluate(() => window.__isite2GlobeControlState?.());
  expect(globalControlState?.autoRotate).toBe(true);

  await africaCard.click();
  await page.getByRole("button", { name: "Back to all" }).click();
  await expect(africaCard).not.toHaveClass(/active/);
  await expect(page.getByRole("button", { name: "Back to all" })).toHaveCount(0);
  await expectGlobeMarkerCount(page, 5);

  await africaCard.click();
  await page.getByRole("button", { name: /Algeria, 9 candidate properties/ }).click();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "country");
  await page.evaluate(() => window.__isite2SelectCountry?.(""));
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "overview");
  await expect(page.getByRole("button", { name: "Back to all" })).toHaveCount(0);
  await expectGlobeMarkerCount(page, 5);
});

test("promotes a selected country into the workspace layout", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, { dataRequests });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 9 candidate properties/ }).click();

  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "country");
  await expect(page.locator(".country-reentry-transition")).toHaveCount(0);
  await expect(page.locator(".country-cloud-transition")).toHaveCount(0);
  await expect(page.locator(".country-cloud-layer-left")).toHaveCount(0);
  await expect(page.locator(".country-cloud-layer-right")).toHaveCount(0);
  await expect(page.locator(".satellite-navigator")).toBeVisible();
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "country_satellite");
  const controlState = await page.evaluate(() => window.__isite2GlobeControlState?.());
  expect(controlState?.autoRotate).toBe(false);
  await expect(page.locator(".workspace-context-bar")).toBeVisible();
  await expect(page.getByLabel("Evidence gaps")).toHaveCount(0);
  await expect(page.getByLabel("Scene distribution")).toBeVisible();
  await expectPropertyCardCount(page, 9);
  await expect(page.locator(".property-list")).not.toContainText("venue centroid");
  await expect(page.locator(".property-list")).not.toContainText("Mock map source");

  const topbarChrome = await page.evaluate(() => {
    const toolbar = document.querySelector(".toolbar")?.getBoundingClientRect();
    const status = document.querySelector(".status-hud")?.getBoundingClientRect();
    return {
      toolbarBottom: toolbar?.bottom || 0,
      statusTop: status?.top || 0,
    };
  });
  expect(topbarChrome.statusTop).toBeGreaterThanOrEqual(topbarChrome.toolbarBottom);

  const widths = await page.evaluate(() => {
    const globe = document.querySelector(".globe-stage")?.getBoundingClientRect().width || 0;
    const panel = document.querySelector(".insight-panel")?.getBoundingClientRect().width || 0;
    return { globe, panel };
  });
  if ((page.viewportSize()?.width || 0) >= 920) {
    expect(widths.panel).toBeGreaterThan(widths.globe);
  } else {
    expect(widths.panel).toBeGreaterThanOrEqual(widths.globe);
  }
  await expect(page.getByRole("button", { name: "Expand map" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Collapse map" })).toHaveCount(0);
});

test("searches property aliases globally and opens the matching property card", async ({ page }) => {
  const dataRequests: string[] = [];
  const target = createPacket(PLACES[0], "stadium", 0, {
    aliases: ["Stade du 5 Juillet"],
  });
  const packets = [target, createPacket(PLACES[2], "mall_mixed_use", 0)];
  await installMockApi(page, { dataRequests, packets });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", {
    waitUntil: "domcontentloaded",
  });
  const search = page.getByRole("combobox", { name: "Search properties" });
  await search.fill("Stade du 5 Juillet");
  const result = page.getByRole("option", { name: /Algiers stadium/ });
  await expect(result).toContainText("Stade du 5 Juillet");
  await result.click();

  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "property");
  await expect(page.locator(".cockpit-dossier")).toContainText("Algiers stadium");
  await expect.poll(() => requestCount(dataRequests, "/properties/search")).toBe(1);
  expect(propertyRequestCountries(dataRequests)).toContain("Algeria");
});

test("downloads the audited full-country workbook from country context", async ({ page }) => {
  const countryExportRequests: string[] = [];
  await installMockApi(page, { dataRequests: [], countryExportRequests });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", {
    waitUntil: "domcontentloaded",
  });
  await page.evaluate(() => window.__isite2SelectCountry?.("Algeria"));
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export country insight" }).click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe(
    "isite_Algeria_standard_report_en_20260810T000000Z.xlsx",
  );
  await expect.poll(() => countryExportRequests.length).toBe(1);
  const requestUrl = new URL(countryExportRequests[0]);
  expect(requestUrl.searchParams.get("country")).toBe("Algeria");
  expect(requestUrl.searchParams.get("locale")).toBe("en");
});

test("shows satellite markers before the country property payload finishes", async ({ page }) => {
  const dataRequests: string[] = [];
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  await installMockApi(page, { dataRequests, propertyDelayMs: 3_000 });

  await page.goto("/ui/", { waitUntil: "domcontentloaded" });
  await expect
    .poll(() => page.evaluate(() => {
      const appWindow = window as typeof window & {
        __isite2SelectCountry?: (country: string) => void;
      };
      return Boolean(appWindow.__isite2SelectCountry);
    }), { timeout: 30_000 })
    .toBe(true);
  await expect.poll(() => requestCount(dataRequests, "/map/country-summary")).toBe(1);

  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
  });

  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "country_satellite");
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-tile-status", /tiles-loading|tiles-ready/);
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll(".satellite-marker").length), { timeout: 2_500 })
    .toBeGreaterThan(0);
  expect(requestCount(dataRequests, "/map/country-summary")).toBe(1);

  await expectPropertyCardCount(page, 9, 6_000);
  await page.evaluate(() => {
    (window as typeof window & { __satelliteNode?: Element | null }).__satelliteNode =
      document.querySelector(".satellite-navigator");
  });
  await clickFirstPropertyCard(page);
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "property_satellite");
  await expect(page.locator(".street-map")).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => {
      const appWindow = window as typeof window & { __satelliteNode?: Element | null };
      return appWindow.__satelliteNode === document.querySelector(".satellite-navigator");
    }))
    .toBe(true);
  expect(consoleErrors.filter((line) => line.includes("Access-Control-Allow-Origin"))).toEqual([]);
});

test("loads property cards only for the selected country and reuses country payloads", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, { dataRequests });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await expect.poll(() => requestCount(dataRequests, "/map/country-summary")).toBe(1);
  expect(propertyRequestCountries(dataRequests)).toEqual([]);

  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
  });
  await expectPropertyCardCount(page, 9);
  expect(propertyRequestCountries(dataRequests)).toEqual(["Algeria"]);

  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Egypt");
  });
  await expectPropertyCardCount(page, 4);
  expect(propertyRequestCountries(dataRequests)).toEqual(["Algeria", "Egypt"]);

  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
  });
  await expectPropertyCardCount(page, 9);
  expect(propertyRequestCountries(dataRequests)).toEqual(["Algeria", "Egypt"]);
});

test("batches large country card rendering and lazy-loads hero images", async ({ page }) => {
  const dataRequests: string[] = [];
  const imageProxyRequests: string[] = [];
  await slowIdleBatches(page);
  await page.route((url) => url.pathname === "/map/hero-image", (route) => {
    imageProxyRequests.push(route.request().url());
    return route.fulfill({ body: EMPTY_PNG, contentType: "image/png" });
  });
  await installMockApi(page, {
    dataRequests,
    packets: LARGE_ALGERIA_CARD_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
  });

  await page.waitForFunction(() => document.querySelectorAll(".property-card").length > 0);
  const firstRenderedCardCount = await page.locator(".property-card").count();
  expect(firstRenderedCardCount).toBeLessThanOrEqual(24);
  expect(imageProxyRequests.length).toBeLessThan(LARGE_ALGERIA_CARD_PACKETS.length);
  await expectPropertyCardCount(page, LARGE_ALGERIA_CARD_PACKETS.length, 16_000);

  const imageRequestCountBeforeScroll = imageProxyRequests.length;
  await page.locator(".property-card").last().scrollIntoViewIfNeeded();
  await expect
    .poll(() => imageProxyRequests.length, { timeout: 5_000 })
    .toBeGreaterThan(imageRequestCountBeforeScroll);
});

test("cancels stale large-country card batches after switching countries", async ({ page }) => {
  const dataRequests: string[] = [];
  await slowIdleBatches(page);
  await installMockApi(page, {
    dataRequests,
    packets: LARGE_ALGERIA_CARD_PACKETS.concat(PACKETS.filter((packet) => packet.entity.country === "Egypt")),
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
  });
  await page.waitForFunction(() => document.querySelectorAll(".property-card").length > 0);
  expect(await page.locator(".property-card").count()).toBeLessThanOrEqual(24);

  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Egypt");
  });

  await expectPropertyCardCount(page, 4);
  await page.waitForTimeout(3_200);
  await expectPropertyCardCount(page, 4);
  await expect(page.locator(".property-list")).not.toContainText("Large Algeria card property");
});

test("public view hides write and connector entrypoints", async ({ page }) => {
  const dataRequests: string[] = [];
  const geocodeRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  const ragRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    geocodeRequests,
    exportRequests,
    ragRequests,
    runtimeConfig: {
      mode: "public_view",
      features: {
        exports: false,
        rag: false,
        connectors: false,
        geocode: false,
      },
    },
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: "Ask iSite2" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export Excel" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export PPT" })).toHaveCount(0);
  await page.getByRole("button", { name: /Algeria, 9 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expect.poll(() => geocodeRequests.length).toBe(0);
  expect(exportRequests).toEqual([]);
  expect(ragRequests).toEqual([]);
});

test("guest click gate requires login after ten UI clicks", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    runtimeConfig: {
      mode: "public_view",
      features: {
        exports: false,
        rag: false,
        connectors: false,
        geocode: false,
      },
      auth: {
        enabled: true,
        guestClickLimit: 10,
        usernameHint: "visitor",
      },
    },
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".auth-status.guest")).toContainText("10 clicks left");

  for (let index = 0; index < 10; index += 1) {
    await page.locator(".globe-stage").click({ position: { x: 40, y: 280 } });
  }
  await expect(page.locator(".auth-status.guest")).toContainText("0 clicks left");

  await page.locator(".globe-stage").click({ position: { x: 40, y: 280 } });
  const dialog = page.getByRole("dialog", { name: "Login required" });
  await expect(dialog).toBeVisible();

  await dialog.getByLabel("Password").fill("wrong");
  await dialog.getByRole("button", { name: "Sign in" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Invalid username or password");

  await dialog.getByLabel("Password").fill("visitor123456");
  await dialog.getByRole("button", { name: "Sign in" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".auth-status.signed-in")).toContainText("visitor");

  await page.locator(".globe-stage").click({ position: { x: 40, y: 280 } });
  await expect(page.getByRole("dialog", { name: "Login required" })).toHaveCount(0);
});

test("authenticated user submits a typed request and reads public updates", async ({ page }, testInfo) => {
  const dataRequests: string[] = [];
  const serviceRequestSubmissions: Array<Record<string, unknown>> = [];
  await installMockApi(page, {
    dataRequests,
    serviceRequestSubmissions,
    productUpdates: [
      {
        id: "update-1",
        category: "scan",
        title: "Egypt scan expanded",
        summary: "Added 8 qualified opportunities in Egypt / Cairo / Stadium.",
        country: "Egypt",
        city: "Cairo",
        scene_type: "stadium",
        scene_label: "Stadium",
        scene_types: ["stadium", "airport_terminal"],
        scene_labels: ["Stadium", "Airport"],
        actual_new_count: 8,
        published_at: "2026-08-25T01:00:00+00:00",
      },
    ],
    runtimeConfig: {
      mode: "public_view",
      features: { exports: false, rag: false, connectors: false, geocode: false },
      auth: { enabled: true, guestClickLimit: 10, usernameHint: "visitor" },
      serviceRequests: {
        enabled: true,
        dailyLimit: 10,
        types: ["scan_enhancement", "feature_request", "ppt_report"],
        updatesLimit: 30,
      },
    },
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.getByTestId("service-request-button").click();
  const login = page.getByRole("dialog", { name: "Login required" });
  await expect(login).toBeVisible();
  await login.getByLabel("Password").fill("visitor123456");
  await login.getByRole("button", { name: "Sign in" }).click();

  const requestDialog = page.getByRole("dialog", { name: "Submit a request" });
  await expect(requestDialog).toBeVisible();
  await requestDialog.getByRole("tab", { name: "Feature idea" }).click();
  await expect(requestDialog.getByLabel("Current workflow")).toBeVisible();
  await requestDialog.getByRole("tab", { name: "PPT report" }).click();
  await expect(requestDialog.getByText("Report language")).toBeVisible();
  await requestDialog.getByRole("tab", { name: "Expand scan" }).click();
  await requestDialog.getByRole("combobox", { name: "Country" }).fill("Egypt");
  await requestDialog.getByRole("option", { name: "Egypt" }).click();
  await requestDialog.getByRole("radio", { name: "National main cities" }).check();
  await requestDialog.getByRole("checkbox", { name: "Airport" }).check();
  await requestDialog.getByRole("checkbox", { name: "Stadium" }).check();
  await requestDialog.getByLabel("Target new qualified properties").fill("10");
  await requestDialog.getByLabel("Contact email").fill("requester@example.com");
  if (process.env.ISITE2_VISUAL_QA === "1") {
    await page.screenshot({
      path: `../../.tmp/request-ui-${testInfo.project.name}.png`,
      animations: "disabled",
    });
  }
  await requestDialog.getByRole("button", { name: "Submit request" }).click();

  await expect(requestDialog.getByTestId("request-success")).toContainText("SR-20260825-ABCD");
  expect(serviceRequestSubmissions).toHaveLength(1);
  expect(serviceRequestSubmissions[0]).toMatchObject({
    request_type: "scan_enhancement",
    country: "Egypt",
    country_input_mode: "catalog",
    city_scope: "national_main_cities",
    city: null,
    city_input_mode: "not_applicable",
    scene_types: ["airport_terminal", "stadium"],
    target_new_qualified_properties: 10,
    contact_email: "requester@example.com",
  });

  await requestDialog.getByRole("button", { name: "Submit another" }).click();
  await requestDialog.getByRole("radio", { name: "Single city" }).check();
  await requestDialog.getByRole("combobox", { name: "Country" }).fill("Atlantis");
  await requestDialog.getByRole("option", { name: /Use.*Atlantis.*custom country/ }).click();
  await expect(requestDialog).toContainText("Custom entry");
  await requestDialog.getByRole("combobox", { name: "City" }).fill("Poseidon");
  await requestDialog.getByRole("option", { name: /Use.*Poseidon.*custom city/ }).click();
  await requestDialog.getByRole("button", { name: "Submit request" }).click();
  await expect(requestDialog.getByTestId("request-success")).toContainText("SR-20260825-ABCD");
  expect(serviceRequestSubmissions).toHaveLength(2);
  expect(serviceRequestSubmissions[1]).toMatchObject({
    country: "Atlantis",
    country_input_mode: "custom",
    city_scope: "single_city",
    city: "Poseidon",
    city_input_mode: "custom",
    scene_types: ["airport_terminal", "stadium"],
  });

  await requestDialog.getByTestId("request-success").getByRole("button", { name: "Close" }).click();
  await page.getByTestId("updates-button").click();
  await expect(page.getByTestId("updates-list")).toContainText("Egypt scan expanded");
  await expect(page.getByTestId("updates-list")).toContainText("Airport");
  await expect(page.getByTestId("updates-list")).toContainText("+8");
});

test("groups newly scanned APAC countries in the overview region distribution", async ({ page }) => {
  const dataRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    exportRequests,
    packets: APAC_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".globe-stage")).toBeVisible();
  await expect(page.getByText("Region distribution", { exact: true })).toBeVisible();
  await expect(page.getByText("Asia Pacific", { exact: true })).toBeVisible();
  await expect(page.getByText("Other regions", { exact: true })).toHaveCount(0);
  await expect(page.locator(".region-row").filter({ hasText: "Asia Pacific" })).toContainText(
    "3 countries",
  );
  const maldivesMarker = page.getByRole("button", {
    name: /Maldives, 1 candidate properties, country display marker/,
  });
  await expect(maldivesMarker).toBeVisible();
  await expect(maldivesMarker).not.toHaveAttribute("title", /.+/);
  await expect(maldivesMarker).toHaveAttribute("data-position-source", "display_anchor");
  await expect(page.getByRole("button", { name: /Sri Lanka, 1 candidate properties/ }))
    .toHaveAttribute("data-marker-detail", /0 mapped cities/);
  await expect(page.getByRole("button", { name: /Cambodia, 1 candidate properties/ }))
    .toHaveAttribute("data-marker-detail", /0 mapped cities/);

  await maldivesMarker.click();
  await expect(page.locator(".panel-head h2")).toContainText("Maldives", { timeout: 10_000 });
  await expectPropertyCardCount(page, 1);
  await expect(page.getByRole("button", { name: "Export Excel" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export PPT" })).toHaveCount(0);
  expect(exportRequests).toEqual([]);
});

test("keeps Cape Verde and Sao Tome in Africa with display anchors", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: AFRICAN_ISLAND_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 2);
  await expect(page.getByText("Africa", { exact: true })).toBeVisible();
  await expect(page.getByText("Other regions", { exact: true })).toHaveCount(0);
  await expect(page.locator(".region-row").filter({ hasText: "Africa" })).toContainText(
    "2 countries",
  );

  const capeVerdeMarker = page.getByRole("button", {
    name: /Cape Verde, 1 candidate properties, country display marker/,
  });
  const saoTomeMarker = page.getByRole("button", {
    name: /Sao Tome and Principe, 1 candidate properties, country display marker/,
  });
  await expect(capeVerdeMarker).toHaveAttribute("data-position-source", "display_anchor");
  await expect(saoTomeMarker).toHaveAttribute("data-position-source", "display_anchor");
});

test("keeps North African countries inside the Africa overview region", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: NORTH_AFRICA_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 5);
  await expect(page.getByText("Africa", { exact: true })).toBeVisible();
  await expect(page.getByText("North Africa", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Other regions", { exact: true })).toHaveCount(0);
  await expect(page.locator(".region-row").filter({ hasText: "Africa" })).toContainText(
    "5 countries",
  );
});

test("uses a compact Middle East and Central Asia overview label", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: MIDDLE_EAST_CENTRAL_ASIA_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 3);
  const regionRow = page.locator(".region-row[data-region='Middle East & Central Asia']");
  const regionTitle = page.locator(".region-row[data-region='Middle East & Central Asia'] > div:first-child strong");
  await expect(regionRow).toBeVisible();
  await expect(regionTitle).toHaveText("ME & C. Asia");
  await expect(regionRow).toContainText("3 countries");
  await expect(page.getByText("Middle East & Central Asia", { exact: true })).toHaveCount(0);
  await expect(regionTitle).toHaveCSS("white-space", "nowrap");
});

test("groups European target countries in Europe and anchors country-name variants accurately", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: EUROPE_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, EUROPE_PACKETS.length);
  await expect(page.getByText("Europe", { exact: true })).toBeVisible();
  await expect(page.getByText("Other regions", { exact: true })).toHaveCount(0);
  await expect(page.locator(".region-row").filter({ hasText: "Europe" })).toContainText(
    `${EUROPE_PACKETS.length} countries`,
  );
  await expect(page.getByRole("button", { name: /Germany, 1 candidate properties/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Greece, 1 candidate properties/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Switzerland, 1 candidate properties, country display marker/ }))
    .toHaveAttribute("data-position-source", "display_anchor");
  await expect(page.getByRole("button", { name: /France, 1 candidate properties, country display marker/ }))
    .toHaveAttribute("data-position-source", "display_anchor");
  await expect(page.getByRole("button", { name: /North Macedonia, 1 candidate properties, country display marker/ }))
    .toHaveAttribute("data-position-source", "display_anchor");
  await expect(page.getByRole("button", { name: /Bosnia and Herzegovina, 1 candidate properties, country display marker/ }))
    .toHaveAttribute("data-position-source", "display_anchor");
  const czechMarker = page.getByRole("button", { name: /Czech Republic, 1 candidate properties/ });
  const czechDetail = await czechMarker.getAttribute("data-marker-detail");
  expect(czechDetail).toContain("0 mapped cities");
  expect(czechDetail).not.toContain("fallback country position");

  await page.getByRole("button", { name: /Europe/ }).click();
  await expectGlobeMarkerCount(page, EUROPE_PACKETS.length);
  await expect(page.locator(".kpi").nth(0)).toContainText(String(EUROPE_PACKETS.length));
  await expect(page.locator(".kpi").nth(1)).toContainText(String(EUROPE_PACKETS.length));
});

test("keeps Other regions after named regions", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: EUROPE_PACKETS.concat(UNMAPPED_REGION_PACKETS),
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, EUROPE_PACKETS.length + 1);
  await expect(page.getByText("Europe", { exact: true })).toBeVisible();
  await expect(page.getByText("Other regions", { exact: true })).toBeVisible();
  const regionNames = await page.evaluate(() =>
    Array.from(document.querySelectorAll(".region-row"))
      .map((row) => row.querySelector("strong")?.textContent?.trim() || ""),
  );
  expect(regionNames).toEqual(["Europe", "Other regions"]);
});

test("classifies former Other-region countries by continent while leaving Russia in Other", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: EUROPE_PACKETS.concat([REUNION_PACKET, RUSSIA_PACKET]),
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, EUROPE_PACKETS.length + 2);
  await expect(page.locator(".region-row[data-region='Europe']")).toContainText(
    `${EUROPE_PACKETS.length} countries`,
  );
  await expect(page.locator(".region-row[data-region='Africa']")).toContainText("1 countries");
  await expect(page.locator(".region-row[data-region='Other regions']")).toContainText("1 countries");

  await page.locator(".region-row[data-region='Other regions']").click();
  await expectGlobeMarkerCount(page, 1);
  await expect(page.getByRole("button", { name: /Russia, 1 candidate properties/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /North Macedonia, 1 candidate properties/ })).toHaveCount(0);

  await page.getByRole("button", { name: /Back to all/ }).click();
  await page.locator(".region-row[data-region='Africa']").click();
  await expectGlobeMarkerCount(page, 1);
  await expect(page.getByRole("button", { name: /Reunion, 1 candidate properties, country display marker/ }))
    .toHaveAttribute("data-position-source", "display_anchor");
});

test("keeps known country-name variants off fallback marker positions", async ({ page }) => {
  await installMockApi(page, {
    dataRequests: [],
    packets: FALLBACK_RISK_COUNTRY_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await expectGlobeMarkerCount(page, 6);
  for (const country of [
    "Central African Republic",
    "Cote d'Ivoire",
    "Czech Republic",
    "Democratic Republic of the Congo",
    "Dominican Republic",
  ]) {
    const marker = page.getByRole("button", { name: new RegExp(`${country.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}, 1 candidate properties`) });
    await expect(marker).toHaveAttribute("data-marker-detail", /0 mapped cities/);
    await expect(marker).not.toHaveAttribute("data-marker-detail", /fallback country position/);
  }
  const barbadosMarker = page.getByRole("button", {
    name: /Barbados, 1 candidate properties, country display marker/,
  });
  await expect(barbadosMarker).toHaveAttribute("data-position-source", "display_anchor");
  await expect(barbadosMarker).not.toHaveAttribute("data-marker-detail", /fallback country position/);
});

test("keeps only the country selector under the left visual stage", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, { dataRequests });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  const stage = page.locator(".globe-stage");

  await expectGlobeMarkerCount(page, 2);
  await expect(stage.locator(".filter-strip")).toHaveCount(0);
  await expect(stage.getByText("All scenes", { exact: true })).toHaveCount(0);
  await expect(stage.getByText("All evidence", { exact: true })).toHaveCount(0);
  await expect(stage.getByText("All actions", { exact: true })).toHaveCount(0);
  await expect(stage.getByText("Primary metric", { exact: true })).toHaveCount(0);
  await expect(stage.getByText("Review", { exact: true })).toHaveCount(0);
  await expect(stage.getByRole("region", { name: "Country scope" })).toBeVisible();
  await expect(stage.getByRole("button", { name: /Country scope:/ })).toBeVisible();
});

test("keeps the country scope results scrollable above the trigger", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, { dataRequests, packets: MANY_COUNTRY_SCOPE_PACKETS });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  const trigger = page.getByRole("button", { name: /Country scope:/ });
  await trigger.click();
  await expect(page.locator(".country-scope-menu")).toBeVisible();

  const layout = await page.evaluate(() => {
    const triggerElement = document.querySelector<HTMLElement>(".country-scope-trigger");
    const menu = document.querySelector<HTMLElement>(".country-scope-menu");
    const list = document.querySelector<HTMLElement>(".country-result-list");
    if (!triggerElement || !menu || !list) {
      return null;
    }
    const triggerRect = triggerElement.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    const scrollTopBefore = list.scrollTop;
    list.scrollTop = 120;
    return {
      triggerTop: triggerRect.top,
      menuTop: menuRect.top,
      menuBottom: menuRect.bottom,
      listClientHeight: list.clientHeight,
      listScrollHeight: list.scrollHeight,
      listRectHeight: listRect.height,
      listOverflowY: getComputedStyle(list).overflowY,
      scrolled: list.scrollTop > scrollTopBefore,
    };
  });

  expect(layout).not.toBeNull();
  expect(layout!.menuTop).toBeGreaterThanOrEqual(0);
  expect(layout!.menuBottom).toBeLessThanOrEqual(layout!.triggerTop - 6);
  expect(layout!.listOverflowY).toBe("auto");
  expect(layout!.listScrollHeight).toBeGreaterThan(layout!.listClientHeight + 80);
  expect(layout!.listRectHeight).toBe(layout!.listClientHeight);
  expect(layout!.scrolled).toBe(true);
  await trigger.click();
  await expect(page.locator(".country-scope-menu")).toHaveCount(0);
});

test("labels country scan maturity without presenting candidate count as market size", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, { dataRequests });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Country scope:/ }).click();

  const algeriaRow = page.locator(".country-result-list .country-row").filter({ hasText: "Algeria" });
  const maturityBadge = algeriaRow.locator('[data-scan-maturity="seed_scan"]');
  await expect(maturityBadge).toHaveText("Seed scan");
  await expect(maturityBadge).toHaveAttribute("title", /sources and scenes still need expansion/i);

  await algeriaRow.click();
  await expect(page.locator('.workspace-context-bar [data-scan-maturity="seed_scan"]')).toHaveText("Seed scan");
});

test("shows airport objective primary metric instead of gateway role on property cards", async ({ page }) => {
  const airportPacket = withEvidence(
    createPacket(PLACES[0], "airport_terminal", 0),
    [
      {
        field_group: "airport_role",
        indicator_name: "gateway_role",
        field_value: "Primary international gateway airport for Algiers.",
      },
      {
        field_group: "annual_passenger_throughput",
        indicator_name: "annual_passenger_throughput",
        field_value: "2024 passenger throughput: 10,000,000 passengers",
      },
    ],
  );
  await installMockApi(page, { dataRequests: [], packets: [airportPacket] });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 1 candidate properties/ }).click();
  await expectPropertyCardCount(page, 1);
  const card = page.locator(".property-card");
  await expect(card).toHaveAttribute("data-action-tone", "primary");
  await expect(card.locator(".card-evidence")).toContainText("2 evidence");
  await expect(card.locator(".card-metric")).toContainText("2024 passenger throughput: 10,000,000 passengers");
  await expect(card.locator(".card-action")).toContainText("Survey First");
  await expect(card).not.toContainText("Primary international gateway airport");
});

test("filters the right panel from scene distribution and sorts by primary metric", async ({ page }) => {
  const dataRequests: string[] = [];
  const panelPackets = [
    withPrimaryEvidence(
      createPacket(PLACES[0], "luxury_hotel_mice", 0),
      "room_count",
      "60 rooms",
    ),
    withPrimaryEvidence(
      createPacket(PLACES[1], "luxury_hotel_mice", 1),
      "room_count",
      "140 rooms",
    ),
    withPrimaryEvidence(
      createPacket(CITY_ONLY_PLACE, "luxury_hotel_mice", 2),
      "room_count",
      "90 rooms",
    ),
    withPrimaryEvidence(
      createPacket(PLACES[0], "stadium", 0),
      "seat_count",
      "18,000 seats",
    ),
  ];
  await installMockApi(page, { dataRequests, packets: panelPackets });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 4 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 4);
  await expectGlobeMarkerCount(page, 3);
  await expect(page.locator(".globe-stage .filter-strip")).toHaveCount(0);
  const requestCountAfterLoad = dataRequests.length;

  const sceneDistribution = page.getByLabel("Scene distribution");
  const hotelScene = sceneDistribution.getByRole("button", { name: /Hotel \/ MICE/ });
  await hotelScene.click();

  await expect(hotelScene).toHaveAttribute("aria-pressed", "true");
  await expectPropertyCardCount(page, 3);
  await expect(page.locator(".globe-stage .filter-strip")).toHaveCount(0);
  await expectGlobeMarkerCount(page, 3);
  await expect.poll(async () => propertyCardTitles(page)).toEqual([
    "Oran luxury hotel mice",
    "Setif luxury hotel mice",
    "Algiers luxury hotel mice",
  ]);
  await expect(page.locator(".property-list")).not.toContainText("Algiers stadium");
  await page.waitForTimeout(250);
  expect(dataRequests.length).toBe(requestCountAfterLoad);

  await hotelScene.click();
  await expect(hotelScene).toHaveAttribute("aria-pressed", "false");
  await expectPropertyCardCount(page, 4);
});

test("prioritizes properties with images before primary metric in card and dense lists", async ({ page }) => {
  const imageFirstPacket = withPrimaryEvidence(
    createPacket(PLACES[0], "luxury_hotel_mice", 0, {
      heroImage: {
        url: "https://images.example.test/algiers-hotel.jpg",
        alt_text: "Algiers hotel exterior",
        source_name: "Official hotel site",
        source_url: "https://example.test/algiers-hotel",
      },
    }),
    "room_count",
    "60 rooms",
  );
  const higherMetricPacket = withPrimaryEvidence(
    createPacket(PLACES[1], "luxury_hotel_mice", 1),
    "room_count",
    "140 rooms",
  );
  const middleMetricPacket = withPrimaryEvidence(
    createPacket(CITY_ONLY_PLACE, "luxury_hotel_mice", 2),
    "room_count",
    "90 rooms",
  );
  await installMockApi(page, {
    dataRequests: [],
    packets: [higherMetricPacket, imageFirstPacket, middleMetricPacket],
  });

  await page.goto("/ui/?mock_globe=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 3 candidate properties/ }).click();
  await expectPropertyCardCount(page, 3);
  await expect.poll(async () => propertyCardTitles(page)).toEqual([
    "Algiers luxury hotel mice",
    "Oran luxury hotel mice",
    "Setif luxury hotel mice",
  ]);

  await page.getByRole("button", { name: "Dense view" }).click();
  await expect(page.locator(".dense-property-row").first()).toContainText(
    "Algiers luxury hotel mice",
  );
});

test("switches the workspace list to dense rows and opens a property detail surface", async ({ page }) => {
  const dataRequests: string[] = [];
  const densePackets = [
    createPacket(PLACES[0], "stadium", 0),
    createPacket(PLACES[1], "stadium", 1),
    createPacket(PLACES[0], "mall_mixed_use", 0),
  ];
  await installMockApi(page, { dataRequests, packets: densePackets });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 3 candidate properties/ }).click();
  const sceneDistribution = page.getByLabel("Scene distribution");
  const stadiumScene = sceneDistribution.getByRole("button", { name: /Stadium/ });
  await stadiumScene.click();

  await page.getByRole("button", { name: "Dense view" }).click();
  await expect(page.locator(".dense-property-list")).toBeVisible();
  await expect(page.locator(".dense-property-row")).toHaveCount(2);
  await expect(page.locator(".dense-property-row").first()).toContainText("stadium");
  await expect(page.locator(".dense-property-row").first()).toContainText("seats");
  await expect(page.locator(".dense-property-row").first()).toContainText("1");

  await page.locator(".dense-property-row").first().click();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "property");
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "property_satellite");
  await expect(page.locator(".dossier")).toBeVisible();
  await expect(page.locator(".cockpit-dossier")).toBeVisible();
  await expect(page.locator(".street-map")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "evidence" })).toBeVisible();
  await expect(page.getByRole("button", { name: "inference" })).toBeVisible();
  await expect(page.getByRole("button", { name: "review" })).toBeVisible();

  await page.getByRole("button", { name: "Back to list" }).click();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "country");
  await expect(stadiumScene).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".dense-property-row")).toHaveCount(2);
});

test("shows a verified public image and source in the property detail cockpit", async ({ page }) => {
  const dataRequests: string[] = [];
  const heroImage: MockHeroImage = {
    url: "https://images.example.test/algiers-stadium.jpg",
    alt_text: "Algiers stadium public facade",
    source_name: "Mock Image Archive",
    source_url: "https://example.org/algiers-stadium-image",
    source_date: "2026-05-01",
    license: "Creative Commons",
  };
  const packets = [
    createPacket(PLACES[0], "stadium", 0, { heroImage }),
    createPacket(PLACES[1], "mall_mixed_use", 0),
  ];
  await page.route((url) => url.pathname === "/map/hero-image", (route) =>
    route.fulfill({ body: EMPTY_PNG, contentType: "image/png" }),
  );
  await installMockApi(page, { dataRequests, packets });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 2 candidate properties/ }).click({ force: true });
  await page.locator(".property-card", { hasText: "Algiers stadium" }).locator("button").click();

  const dossier = page.locator(".cockpit-dossier");
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "property");
  await expect(dossier.locator(".dossier-visual-cockpit")).toBeVisible();
  await expect(dossier.locator(".dossier-image-frame img")).toHaveAttribute(
    "src",
    `/map/hero-image?url=${encodeURIComponent(heroImage.url)}`,
  );
  await expect(dossier.locator(".dossier-image-frame img")).toHaveAttribute("alt", heroImage.alt_text);
  await expect(dossier.getByRole("link", { name: /Mock Image Archive/ })).toHaveAttribute("href", heroImage.source_url);
  await expect(dossier).toContainText("2026-05-01");
  await expect(dossier).toContainText("Creative Commons");
  await expect(dossier.locator(".primary-metric-card")).toContainText("seats");
  await expect(dossier.locator(".action-card")).toHaveAttribute("data-tone", "primary");
  await expect(dossier.locator(".dossier-status-pill[data-tone='review']")).toContainText("Review");
  await expect(dossier.getByText("Action Class", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "evidence" })).toBeVisible();
});

test("toggles property satellite overlays between Ookla mobile and public footfall", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const dataRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    packets: [
      createPacket(PLACES[0], "stadium", 0),
      createPacket(PLACES[1], "mall_mixed_use", 0),
    ],
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 2 candidate properties/ }).click();
  await expectPropertyCardCount(page, 2);
  await clickFirstPropertyCard(page);
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "property_satellite");
  await expect(page.getByRole("button", { name: /Satellite/ })).toHaveClass(/active/);

  await page.locator('[data-overlay-mode="mobile_network"]').click();
  await expect(page.getByRole("button", { name: /Network/ })).toHaveClass(/active/);
  await expect(page.locator(".property-overlay-status")).toContainText("Hover tiles or center dots", { timeout: 10_000 });
  await expect(page.locator(".network-overlay-legend")).toContainText("Network Experience");
  await expect(page.locator(".network-overlay-legend")).toContainText("5 km");
  await expect(page.locator(".network-overlay-legend")).toContainText("Cyan dots mark tile centers");
  await expect(page.locator(".network-overlay-legend")).toContainText("Poor");
  await expect(page.locator(".network-overlay-legend")).toContainText("Excellent");
  await expect
    .poll(() => dataRequests.some((request) => request.includes("layer=mobile_network") && request.includes("radius_m=5000")))
    .toBe(true);

  await page.evaluate(() =>
    document.querySelector<HTMLButtonElement>('[data-overlay-mode="footfall"]')?.click(),
  );
  await expect(page.getByRole("button", { name: /Footfall/ })).toHaveClass(/active/);
  await expect(page.locator(".property-overlay-status")).toContainText("Hover heat cells", { timeout: 10_000 });
  await expect(page.locator(".footfall-overlay-legend")).toContainText("Footfall");
  await expect(page.locator(".footfall-overlay-legend")).toContainText("public observations");
  await expect
    .poll(() => dataRequests.some((request) => request.includes("layer=footfall") && request.includes("radius_m=5000")))
    .toBe(true);

  await page.evaluate(() =>
    document.querySelector<HTMLButtonElement>('[data-overlay-mode="satellite"]')?.click(),
  );
  await expect(page.getByRole("button", { name: /Satellite/ })).toHaveClass(/active/);
  await expect(page.locator(".property-overlay-status")).toHaveCount(0);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("shows mobile network values when hovering a rendered overlay tile", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    packets: [
      createPacket(PLACES[0], "stadium", 0),
      createPacket(PLACES[1], "mall_mixed_use", 0),
    ],
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&real_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 2 candidate properties/ }).click();
  await expectPropertyCardCount(page, 2);
  await clickFirstPropertyCard(page);
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "property_satellite");
  await page.locator('[data-overlay-mode="mobile_network"]').click();
  await expect(page.locator(".property-overlay-status")).toContainText("Hover tiles or center dots", { timeout: 10_000 });

  const stage = page.locator(".satellite-navigator");
  const box = await stage.boundingBox();
  expect(box).not.toBeNull();
  if (!box) {
    return;
  }
  await page.mouse.move(box.x + box.width * 0.47, box.y + box.height * 0.5);
  await expect(page.locator(".map-hover-tooltip[data-tooltip-variant='network']")).toContainText("Mbps down", { timeout: 10_000 });
  await expect(page.locator(".map-hover-tooltip[data-tooltip-variant='network']")).toContainText("tests");
});

test("shows aggregated complaint signals and an explicit compliant-data empty state", async ({ page }) => {
  const dataRequests: string[] = [];
  const populated = createPacket(PLACES[0], "stadium", 0) as ReturnType<typeof createPacket> & {
    network_signals?: Record<string, unknown>;
  };
  populated.network_signals = {
    complaints: {
      valid_complaint_count: 4,
      weighted_complaint_count: 3.4,
      source_count: 2,
      category_counts: {
        no_signal: 2,
        weak_signal: 1,
        network_outage: 1,
      },
      pressure_level: "high",
      pressure_percentile: 0.86,
      confidence: "medium",
      period_days: 365,
      latest_observed_at: "2026-05-18T00:00:00Z",
      data_freshness: "recent",
    },
    network_validation_priority: "High",
  };
  const empty = createPacket(PLACES[1], "mall_mixed_use", 0);
  await installMockApi(page, { dataRequests, packets: [populated, empty] });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 2 candidate properties/ }).click();
  await page.locator(".property-card", { hasText: "Algiers stadium" }).locator("button").click();

  const populatedPanel = page.locator(".complaint-signals-panel");
  await expect(populatedPanel).toHaveAttribute("data-state", "available");
  await expect(populatedPanel).toContainText("Property-level public signals");
  await expect(populatedPanel).toContainText("Valid complaints");
  await expect(populatedPanel).toContainText("4");
  await expect(populatedPanel).toContainText("No signal 2");
  await expect(populatedPanel).toContainText("Weak coverage 1");
  await expect(populatedPanel).toContainText("Network outage 1");

  await page.getByRole("button", { name: "Back to list" }).click();
  await page.locator(".property-card", { hasText: "Oran mall mixed use" }).locator("button").click();
  const emptyPanel = page.locator(".complaint-signals-panel");
  await expect(emptyPanel).toHaveAttribute("data-state", "empty");
  await expect(emptyPanel).toContainText("No compliant property-level network complaints are currently available.");
});

test("refreshes localized property detail without leaving the selected property", async ({ page }) => {
  const dataRequests: string[] = [];
  const packets = [
    createPacket(PLACES[0], "stadium", 0),
    createPacket(PLACES[1], "mall_mixed_use", 0),
  ];
  await installMockApi(page, { dataRequests, packets });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 2 candidate properties/ }).click();
  await expectPropertyCardCount(page, 2);
  await page.locator(".property-card", { hasText: "Algiers stadium" }).locator("button").click();

  const dossier = page.locator(".cockpit-dossier");
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "property");
  await expect(dossier).toContainText("Mock evidence supports a high-value opportunity.");

  const languageToggle = page.getByTestId("language-toggle");
  await languageToggle.getByRole("button", { name: "中文" }).click();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "property");
  await expect(dossier.locator(".dossier-hero h3")).toContainText("Algiers stadium");
  await expect
    .poll(() => requestCountWithParam(dataRequests, "/properties", "locale", "zh"), { timeout: 10_000 })
    .toBeGreaterThan(0);
  await expect(dossier).toContainText("中文推荐理由：公开证据支持该物业为高价值机会点。");
  await expect(dossier).toContainText("中文下一步：核验坐标并补查室分建设状态。");
  await expect(dossier).toContainText("中文主指标：");
  await expect(dossier).toContainText("体育场");

  await languageToggle.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "property");
  await expect
    .poll(() => requestCountWithParam(dataRequests, "/properties", "locale", "en"), { timeout: 10_000 })
    .toBeGreaterThan(1);
  await expect(dossier).toContainText("Mock evidence supports a high-value opportunity.");
  await expect(dossier).toContainText("Validate coordinate and indoor build status.");
});

test("shows AI Thinking while localization refreshes in country and city workspaces", async ({ page }) => {
  const dataRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    localizationDelayMs: 850,
    packets: DENSE_ALGIERS_PROPERTY_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
  });
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "country");
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "country_satellite");

  const languageToggle = page.getByTestId("language-toggle");
  await languageToggle.getByRole("button", { name: "中文" }).click();
  const localizationStatus = page.getByRole("status").filter({ hasText: "AI Thinking" });
  await expect(localizationStatus).toBeVisible();
  await expect(localizationStatus).toContainText("Loading localized text");
  await expect(page.locator(".app-shell")).toHaveAttribute("data-localization-loading", "true");
  await expect
    .poll(() => requestCountWithParam(dataRequests, "/properties", "locale", "zh"), { timeout: 10_000 })
    .toBeGreaterThan(0);
  await expect(page.locator(".app-shell")).toHaveAttribute("data-localization-loading", "false", { timeout: 10_000 });
  await expect(localizationStatus).toHaveCount(0);
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "country");

  await expect
    .poll(() => page.evaluate(() => Array.from(document.querySelectorAll<HTMLButtonElement>('[data-satellite-marker-kind="city"]'))
      .some((button) => button.getAttribute("aria-label")?.includes("Algiers, Algeria"))), { timeout: 20_000 })
    .toBe(true);
  const selectedCity = await page.evaluate(() => {
    const city = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-satellite-marker-kind="city"]'))
      .find((button) => button.getAttribute("aria-label")?.includes("Algiers, Algeria"));
    city?.click();
    return Boolean(city);
  });
  expect(selectedCity).toBe(true);
  await expect(page.locator(".panel-head h2")).toContainText("Algiers", { timeout: 20_000 });
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "city");

  await languageToggle.getByRole("button", { name: "EN", exact: true }).click();
  await expect(localizationStatus).toBeVisible();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-localization-loading", "true");
  await expect
    .poll(() => requestCountWithParam(dataRequests, "/properties", "locale", "en"), { timeout: 10_000 })
    .toBeGreaterThan(1);
  await expect(page.locator(".app-shell")).toHaveAttribute("data-localization-loading", "false", { timeout: 10_000 });
  await expect(localizationStatus).toHaveCount(0);
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "city");
  await expect(page.locator(".panel-head h2")).toContainText("Algiers");
});

test("shows an explicit no-image state in the property detail cockpit", async ({ page }) => {
  const dataRequests: string[] = [];
  const packets = [
    createPacket(PLACES[0], "stadium", 0),
  ];
  await installMockApi(page, { dataRequests, packets });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Algeria, 1 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 1);
  await clickFirstPropertyCard(page);

  const dossier = page.locator(".cockpit-dossier");
  await expect(page.locator(".app-shell")).toHaveAttribute("data-view-mode", "property");
  await expect(dossier.locator(".dossier-image-frame img")).toHaveCount(0);
  await expect(dossier).toContainText("No verified public image");
  await expect(dossier).toContainText("Satellite maps, screenshots, and generated visuals are not used as real property images.");
  await expect(page.getByRole("button", { name: "evidence" })).toBeVisible();
  await expect(page.getByRole("button", { name: "inference" })).toBeVisible();
  await expect(page.getByRole("button", { name: "review" })).toBeVisible();
});

test("requests the global candidate pool for the opportunity globe", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  const dataRequests: string[] = [];
  const geocodeRequests: string[] = [];
  const scanRunRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  const ragRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    geocodeRequests,
    scanRunRequests,
    exportRequests,
    ragRequests,
  });

  await page.goto("/ui/?mock_globe=1&view=globe", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".globe-stage")).toBeVisible();
  await expect(page.getByText("Scan", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Export Run", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run scan" })).toHaveCount(0);
  await expectDataRequestCount(dataRequests, 1);
  expect(scanRunRequests).toEqual([]);
  await expectPropertyCardCount(page, 0);
  await expectGlobeMarkerCount(page, 2);
  await expectKpiLabels(page, ["Countries", "Candidates", "Evidence"]);
  await expect(page.getByText("Region distribution", { exact: true })).toBeVisible();
  await expect(page.getByText("Africa", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Algeria, 9 candidate properties/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Egypt, 4 candidate properties/ })).toBeVisible();
  expect(geocodeRequests).toEqual([]);
  await expect(page.locator(".country-rail")).toHaveCount(0);
  await expectGlobeMode(page, "overview");

  await page.getByRole("button", { name: /Algeria, 9 candidate properties/ }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 9);
  await expectGlobeMarkerCount(page, 3);
  await expectKpiLabels(page, ["Cities", "Candidates", "Evidence"]);
  const mediaBox = await firstPropertyMediaBox(page);
  expect(mediaBox?.width ?? 0).toBeGreaterThan(80);
  expect(mediaBox?.height ?? 0).toBeGreaterThan(100);
  await expect(page.getByRole("button", { name: "Export Excel" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export PPT" })).toHaveCount(0);
  expect(exportRequests).toEqual([]);
  expect(geocodeRequests).toEqual([]);
  await expectGlobeMode(page, "focused");

  await page.getByRole("button", { name: /City scope: All cities in Algeria/ }).click();
  const cityRows = page.locator(".country-result-list .country-row");
  await expect(cityRows.nth(0)).toContainText("Algiers");
  await expect(cityRows.nth(0).locator("strong")).toHaveText("4");
  await expect(cityRows.nth(1)).toContainText("Oran");
  const citySearch = page.getByRole("textbox", { name: "City search" });
  await citySearch.fill("Setif");
  await expect(cityRows).toHaveCount(1);
  await expect(cityRows.first()).toContainText("Setif");
  await citySearch.fill("");
  await page.locator(".country-scope-menu .country-row.all").click();

  await expect(page.getByRole("button", { name: /Algiers, Algeria, 4 candidate properties/ }))
    .toHaveAttribute("data-marker-detail", /city satellite marker/);
  await expect(page.getByRole("button", { name: /Setif, Algeria, 1 candidate properties/ }))
    .toHaveAttribute("data-marker-detail", /city satellite marker/);
  await expect(page.getByRole("button", { name: /Algiers, Algeria, 4 candidate properties/ }))
    .not.toHaveAttribute("title", /.+/);
  await page.getByRole("button", { name: /Algiers, Algeria, 4 candidate properties/ }).hover();
  await expect(page.locator(".map-hover-tooltip")).toContainText("Algiers");
  await expect(page.locator(".map-hover-tooltip")).toContainText("4 candidates");

  await page.getByRole("button", { name: /Algiers, Algeria/ }).click({ force: true });
  await expect(page.locator(".panel-head h2")).toContainText("Algiers", { timeout: 10_000 });
  await expectPropertyCardCount(page, 4);
  await expect(page.getByLabel("Evidence gaps")).toHaveCount(0);
  await expect(page.locator(".property-list")).not.toContainText("venue centroid");
  await expect(page.locator(".property-list")).not.toContainText("city centroid");
  await expect(page.locator(".property-list")).not.toContainText("Map Source");
  await expect(page.locator(".property-list")).not.toContainText("Coordinate");
  await expectGlobeMarkerCount(page, 4);
  await expectGlobeMode(page, "focused");

  await clickFirstPropertyCard(page);
  await expect(page.locator(".dossier")).toBeVisible();
  await expect(page.locator(".cockpit-dossier")).toBeVisible();
  await expect(page.locator(".cockpit-dossier")).toContainText("Coordinate");
  await expect(page.locator(".cockpit-dossier")).toContainText("Map Source");
  await expect(page.locator(".street-map")).toHaveCount(0);
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "property_satellite");
  await expectPropertyCardCount(page, 0);
  await expectGlobeMode(page, "focused");

  await page.getByRole("button", { name: "Ask iSite2" }).click();
  const ragDialog = page.getByRole("dialog", { name: "Ask iSite2 RAG assistant" });
  await expect(ragDialog).toBeVisible();
  await expect(ragDialog).toContainText("Algiers airport terminal");
  await expect(ragDialog).toContainText("Evidence-backed retrieval ready");
  await ragDialog.getByRole("textbox", { name: "Question" }).fill(
    "Why is this property recommended?",
  );
  await ragDialog.getByRole("button", { name: "Send" }).click();
  await expect(ragDialog).toContainText("Mock evidence-backed answer");
  await expect(ragDialog).toContainText("Mock Airport Authority");
  expect(ragRequests.every((request) => !("scan_run_id" in request.body))).toBe(true);
  await ragDialog.getByRole("button", { name: "Close Ask iSite2" }).click();

  await page.getByRole("button", { name: "Back to list" }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algiers", { timeout: 10_000 });
  await expectPropertyCardCount(page, 4);

  await page.getByRole("button", { name: "All cities in Algeria" }).click();
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 10_000 });
  await expectPropertyCardCount(page, 9);
  await expectGlobeMarkerCount(page, 3);

  await page.getByRole("button", { name: /Setif, Algeria/ }).click({ force: true });
  await expect(page.locator(".panel-head h2")).toContainText("Setif", { timeout: 10_000 });
  await expectPropertyCardCount(page, 1);
  await expect(page.getByRole("button", { name: "Export PPT" })).toHaveCount(0);
  expect(exportRequests).toEqual([]);

  await page.getByRole("button", { name: /City scope: Setif/ }).click({ force: true });
  await page.getByRole("button", { name: "Back to countries" }).click();
  await expect(page.locator(".panel-head h2")).toContainText("All candidate countries", { timeout: 10_000 });
  await expectPropertyCardCount(page, 0);
  await expectGlobeMarkerCount(page, 2);
  await expectKpiLabels(page, ["Countries", "Candidates", "Evidence"]);
  await expectGlobeMode(page, "overview");

  expectDataRequestsUseGlobalCandidatePool(dataRequests);
  expect(scanRunRequests).toEqual([]);

  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("renders real country overlay markers in overview mode", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const dataRequests: string[] = [];
  const scanRunRequests: string[] = [];
  await installMockApi(page, { dataRequests, scanRunRequests });

  await page.goto("/ui/?view=globe", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".overview-globe-navigator")).toHaveCount(0);
  await expect(page.locator(".satellite-navigator")).toHaveCount(0);
  await expect(page.locator(".globe-stage canvas")).toBeVisible({ timeout: 60_000 });
  const assetState = await page.evaluate(() => window.__isite2GlobeAssetState?.());
  expect(assetState?.globeImageUrl).toContain("earth-blue-marble.jpg");
  expect(assetState?.bumpImageUrl).toContain("earth-topology.png");
  await expect(page.locator(".marker-overlay-layer")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".marker-overlay-layer .country-marker")).toHaveCount(2, { timeout: 60_000 });
  await expectGlobeMode(page, "overview");
  const controlState = await page.evaluate(() => window.__isite2GlobeControlState?.());
  expect(controlState?.autoRotate).toBe(true);
  expect(scanRunRequests).toEqual([]);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("hides globe markers after they rotate behind the earth", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await installMockApi(page, { dataRequests: [], scanRunRequests: [] });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&test_pov=0,-160", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".marker-overlay-layer")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".marker-overlay-layer .country-marker")).toHaveCount(2, { timeout: 60_000 });

  await waitForHiddenMarkerLabels(page, [
    "Algeria, 9 candidate properties",
    "Egypt, 4 candidate properties",
  ]);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("shows dense country city markers in the satellite navigator", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const dataRequests: string[] = [];
  const exportRequests: Array<{ path: string; body: Record<string, unknown> }> = [];
  await installMockApi(page, {
    dataRequests,
    exportRequests,
    packets: DENSE_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".marker-overlay-layer")).toBeVisible({ timeout: 60_000 });
  await expect
    .poll(() => page.evaluate(() => {
      const appWindow = window as typeof window & {
        __isite2SelectCountry?: (country: string) => void;
      };
      return Boolean(appWindow.__isite2SelectCountry);
    }), { timeout: 60_000 })
    .toBe(true);
  const selectedCountry = await page.evaluate(() => {
    const appWindow = window as typeof window & {
      __isite2SelectCountry?: (country: string) => void;
    };
    appWindow.__isite2SelectCountry?.("Algeria");
    return Boolean(appWindow.__isite2SelectCountry);
  });
  expect(selectedCountry).toBe(true);
  await expect(page.locator(".panel-head h2")).toContainText("Algeria", { timeout: 20_000 });
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "country_satellite");
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll('[data-satellite-marker-kind="city_cluster"]').length), { timeout: 20_000 })
    .toBeGreaterThan(0);
  const clusteredState = await page.evaluate(() => {
    const labels = Array.from(document.querySelectorAll<HTMLElement>(".satellite-marker-label"))
      .map((element) => element.childNodes[0]?.textContent?.trim() || "");
    return {
      cityCount: document.querySelectorAll('[data-satellite-marker-kind="city"]').length,
      clusterCount: document.querySelectorAll('[data-satellite-marker-kind="city_cluster"]').length,
      propertyCount: document.querySelectorAll('[data-satellite-marker-kind="property"]').length,
      markerCount: document.querySelectorAll(".satellite-marker").length,
      labels,
      detail: document.querySelector('[data-satellite-marker-kind="city_cluster"]')?.getAttribute("data-marker-detail") || "",
    };
  });
  expect(clusteredState.propertyCount).toBe(0);
  expect(clusteredState.clusterCount).toBeGreaterThan(0);
  expect(clusteredState.markerCount).toBeLessThan(DENSE_ALGERIA_PLACES.length);
  expect(clusteredState.labels.every((label) => !label.includes(", Algeria"))).toBe(true);
  expect(clusteredState.detail).toContain("Algeria");
  const expandedCluster = await page.evaluate(() => {
    const cluster = document.querySelector<HTMLButtonElement>('[data-satellite-marker-kind="city_cluster"]');
    cluster?.click();
    return Boolean(cluster);
  });
  expect(expandedCluster).toBe(true);
  await expect
    .poll(() => page.locator(".satellite-navigator").getAttribute("data-expanded-satellite-cluster"))
    .toContain("satellite_city_cluster::");
  const spideredCluster = await page.evaluate(() => {
    const cluster = document.querySelector<HTMLButtonElement>('[data-satellite-marker-kind="city_cluster"].active')
      || document.querySelector<HTMLButtonElement>('[data-satellite-marker-kind="city_cluster"]');
    cluster?.click();
    return Boolean(cluster);
  });
  expect(spideredCluster).toBe(true);
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll(".satellite-marker.spider-child").length), { timeout: 20_000 })
    .toBeGreaterThan(1);
  const selectedCity = await page.evaluate(() => {
    const city = Array.from(document.querySelectorAll<HTMLButtonElement>(".satellite-marker"))
      .find((button) => button.getAttribute("aria-label")?.includes("Bir Mourad Rais, Algeria"));
    if (city) {
      window.setTimeout(() => city.click(), 0);
    }
    return Boolean(city);
  });
  expect(selectedCity).toBe(true);
  await expect(page.locator(".panel-head h2")).toContainText("Bir Mourad Rais", { timeout: 20_000 });

  await expect(page.getByRole("button", { name: "Export Excel" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export PPT" })).toHaveCount(0);
  expect(exportRequests).toEqual([]);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

test("clusters dense city property markers in the satellite navigator", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const dataRequests: string[] = [];
  await installMockApi(page, {
    dataRequests,
    packets: DENSE_ALGIERS_PROPERTY_PACKETS,
  });

  await page.goto("/ui/?mock_globe=1&mock_cluster=1&mock_satellite=1", { waitUntil: "domcontentloaded" });
  await expect
    .poll(() => page.evaluate(() => {
      const appWindow = window as typeof window & {
        __isite2SelectCountry?: (country: string) => void;
      };
      appWindow.__isite2SelectCountry?.("Algeria");
      return Boolean(appWindow.__isite2SelectCountry);
    }), { timeout: 60_000 })
    .toBe(true);
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "country_satellite");
  await expect
    .poll(() => page.evaluate(() => Array.from(document.querySelectorAll<HTMLButtonElement>('[data-satellite-marker-kind="city"]'))
      .some((button) => button.getAttribute("aria-label")?.includes("Algiers, Algeria"))), { timeout: 20_000 })
    .toBe(true);
  const selectedCity = await page.evaluate(() => {
    const city = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-satellite-marker-kind="city"]'))
      .find((button) => button.getAttribute("aria-label")?.includes("Algiers, Algeria"));
    city?.click();
    return Boolean(city);
  });
  expect(selectedCity).toBe(true);
  await expect(page.locator(".panel-head h2")).toContainText("Algiers", { timeout: 20_000 });
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "city_satellite");
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll('[data-satellite-marker-kind="property_cluster"]').length), { timeout: 20_000 })
    .toBeGreaterThan(0);

  const clusteredState = await page.evaluate(() => ({
    clusterCount: document.querySelectorAll('[data-satellite-marker-kind="property_cluster"]').length,
    propertyCount: document.querySelectorAll('[data-satellite-marker-kind="property"]').length,
    markerCount: document.querySelectorAll(".satellite-marker").length,
    detail: document.querySelector('[data-satellite-marker-kind="property_cluster"]')?.getAttribute("data-marker-detail") || "",
  }));
  expect(clusteredState.clusterCount).toBeGreaterThan(0);
  expect(clusteredState.markerCount).toBeLessThan(DENSE_ALGIERS_PROPERTY_PACKETS.length);
  expect(clusteredState.detail).toContain("property cluster satellite marker");
  if ((page.viewportSize()?.width || 0) >= 920) {
    await page.locator('[data-satellite-marker-kind="property_cluster"]').first().hover();
    await expect(page.locator(".map-hover-tooltip")).toContainText(/sites/);
  }

  const expandedCluster = await page.evaluate(() => {
    const cluster = document.querySelector<HTMLButtonElement>('[data-satellite-marker-kind="property_cluster"]');
    cluster?.click();
    return Boolean(cluster);
  });
  expect(expandedCluster).toBe(true);
  await expect
    .poll(() => page.locator(".satellite-navigator").getAttribute("data-expanded-satellite-cluster"))
    .toContain("satellite_property_cluster::");

  const spideredCluster = await page.evaluate(() => {
    const cluster = document.querySelector<HTMLButtonElement>('[data-satellite-marker-kind="property_cluster"].active')
      || document.querySelector<HTMLButtonElement>('[data-satellite-marker-kind="property_cluster"]');
    cluster?.click();
    return Boolean(cluster);
  });
  expect(spideredCluster).toBe(true);
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll(".satellite-marker.spider-child").length), { timeout: 20_000 })
    .toBeGreaterThan(1);

  const singleMarkerState = await page.evaluate(() => {
    const marker = document.querySelector<HTMLElement>('[data-satellite-marker-kind="property"].spider-child');
    return {
      coreText: marker?.querySelector(".satellite-marker-core")?.textContent?.trim() || "",
      ariaLabel: marker?.getAttribute("aria-label") || "",
      detail: marker?.getAttribute("data-marker-detail") || "",
    };
  });
  expect(singleMarkerState.coreText).not.toBe("1");
  expect(singleMarkerState.ariaLabel).toContain("Algiers dense property");
  expect(singleMarkerState.detail).toContain("property satellite marker");

  const selectedProperty = await page.evaluate(() => {
    const property = document.querySelector<HTMLButtonElement>('[data-satellite-marker-kind="property"].spider-child');
    property?.click();
    return Boolean(property);
  });
  expect(selectedProperty).toBe(true);
  await expect(page.locator(".satellite-navigator")).toHaveAttribute("data-satellite-mode", "property_satellite");
  await expect(page.locator(".dossier-hero h3")).toContainText("Algiers dense property", { timeout: 20_000 });

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !isIgnorableConsoleError(line))).toEqual([]);
});

async function expectDataRequestCount(dataRequests: string[], expectedMinimum: number) {
  await expect
    .poll(() => dataRequests.length, { timeout: 30_000 })
    .toBeGreaterThanOrEqual(expectedMinimum);
}

function requestCount(requests: string[], pathname: string) {
  return requests.filter((request) => new URL(request).pathname === pathname).length;
}

function normalizeMockSearchText(value: string) {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function requestCountWithParam(requests: string[], pathname: string, key: string, value: string) {
  return requests.filter((request) => {
    const url = new URL(request);
    return url.pathname === pathname && url.searchParams.get(key) === value;
  }).length;
}

function propertyRequestCountries(requests: string[]) {
  return requests
    .filter((request) => new URL(request).pathname === "/properties")
    .map((request) => new URL(request).searchParams.get("country") || "");
}

async function slowIdleBatches(page: Page, delayMs = 3_000) {
  await page.addInitScript((delay) => {
    window.requestIdleCallback = (callback: IdleRequestCallback) =>
      window.setTimeout(
        () => callback({ didTimeout: false, timeRemaining: () => 50 }),
        delay,
      );
    window.cancelIdleCallback = (handle: number) => window.clearTimeout(handle);
  }, delayMs);
}

async function expectPropertyCardCount(page: Page, expected: number, timeout = 10_000) {
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll(".property-card").length), {
      timeout,
    })
    .toBe(expected);
}

function propertyCardTitles(page: Page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll(".property-card h3"))
      .map((element) => element.textContent?.trim() || ""),
  );
}

async function expectGlobeMarkerCount(page: Page, expected: number, timeout = 10_000) {
  await expect
    .poll(() => page.evaluate(() =>
      document.querySelectorAll(".city-marker, .satellite-marker").length
    ), {
      timeout,
    })
    .toBe(expected);
}

async function expectKpiLabels(page: Page, expected: string[], timeout = 10_000) {
  await expect
    .poll(() => page.evaluate(() =>
      Array.from(document.querySelectorAll(".panel-head .kpi span"))
        .map((element) => element.textContent?.trim() || ""),
    ), { timeout })
    .toEqual(expected);
}

function visibleRealClusterMarkerCount(page: Page) {
  return page.evaluate(() => {
    return Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .cluster-marker"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        return style.visibility !== "hidden" && style.pointerEvents !== "none";
      })
      .length;
  });
}

function visibleRealCountryMarkerCount(page: Page) {
  return page.evaluate(() => {
    return Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .country-marker"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        return style.visibility !== "hidden" && style.pointerEvents !== "none";
      })
      .length;
  });
}

function visibleSpiderChildCount(page: Page) {
  return page.evaluate(() => {
    return Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .spider-child"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        return style.visibility !== "hidden" && style.pointerEvents !== "none";
      })
      .length;
  });
}

async function waitForVisibleMarkerLabels(page: Page, labels: string[], timeout = 20_000) {
  await page.waitForFunction((expectedLabels) => {
    return expectedLabels.every((label) => {
      const target = Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .country-marker"))
        .find((element) => element.getAttribute("aria-label") === label);
      if (!target) {
        return false;
      }
      const style = window.getComputedStyle(target);
      return style.visibility !== "hidden" && style.pointerEvents !== "none";
    });
  }, labels, { timeout });
}

async function waitForHiddenMarkerLabels(page: Page, labels: string[], timeout = 20_000) {
  await page.waitForFunction((expectedLabels) => {
    return expectedLabels.every((label) => {
      const target = Array.from(document.querySelectorAll<HTMLElement>(".marker-overlay-layer .country-marker"))
        .find((element) => element.getAttribute("aria-label") === label);
      if (!target) {
        return true;
      }
      const style = window.getComputedStyle(target);
      return style.visibility === "hidden" && style.pointerEvents === "none";
    });
  }, labels, { timeout });
}

function firstPropertyMediaBox(page: Page) {
  return page.evaluate(() => {
    const element = document.querySelector<HTMLElement>(".property-card img, .hero-fallback");
    if (!element) {
      return null;
    }
    const rect = element.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  });
}

async function clickFirstPropertyCard(page: Page) {
  const clicked = await page.evaluate(() => {
    const button = document.querySelector<HTMLButtonElement>(".property-card button");
    button?.click();
    return Boolean(button);
  });
  expect(clicked).toBe(true);
}

async function expectGlobeMode(page: Page, mode: "overview" | "focused") {
  await expect(page.locator(".globe-stage")).toHaveAttribute("data-globe-mode", mode, {
    timeout: 20_000,
  });
}

type MockApiOptions = {
  dataRequests: string[];
  geocodeRequests?: string[];
  scanRunRequests?: string[];
  exportRequests?: Array<{ path: string; body: Record<string, unknown> }>;
  countryExportRequests?: string[];
  ragRequests?: Array<{ path: string; body: Record<string, unknown> }>;
  discoveryRequests?: string[];
  serviceRequestSubmissions?: Array<Record<string, unknown>>;
  productUpdates?: Array<Record<string, unknown>>;
  runtimeConfig?: {
    mode: string;
    features: {
      exports: boolean;
      rag: boolean;
      connectors: boolean;
      geocode: boolean;
    };
    auth?: {
      enabled: boolean;
      guestClickLimit: number;
      usernameHint: string;
    };
    serviceRequests?: {
      enabled: boolean;
      dailyLimit: number;
      types: string[];
      updatesLimit: number;
    };
    map?: {
      satelliteTileTemplate: string;
      satelliteTileSize?: number;
      satelliteAttribution: string;
      propertyOverlayTemplate?: string;
      footfallProvider?: {
        provider: string;
        configured: boolean;
        requiresApiKey: boolean;
        endpointConfigured?: boolean;
      };
    };
  };
  countrySummaryDelayMs?: number;
  localizationDelayMs?: number;
  discoveryDelayMs?: number;
  propertyDelayMs?: number;
  citySummaryFailureCount?: number;
  propertyFailureCount?: number;
  packets?: Array<ReturnType<typeof createPacket>>;
};

async function installMockApi(page: Page, {
  dataRequests,
  geocodeRequests = [],
  scanRunRequests = [],
  exportRequests = [],
  countryExportRequests = [],
  ragRequests = [],
  discoveryRequests = [],
  serviceRequestSubmissions = [],
  productUpdates = [],
  runtimeConfig = {
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
      satelliteAttribution: "Mock satellite attribution",
      propertyOverlayTemplate: "/map/property-overlays/{property_id}?layer={layer}&radius_m={radius_m}",
      footfallProvider: {
        provider: "public_open_data",
        configured: true,
        requiresApiKey: false,
        endpointConfigured: false,
      },
    },
  },
  countrySummaryDelayMs = 0,
  localizationDelayMs = 0,
  discoveryDelayMs = 0,
  propertyDelayMs = 0,
  citySummaryFailureCount = 0,
  propertyFailureCount = 0,
  packets = PACKETS,
}: MockApiOptions) {
  let apiPackets = [...packets];
  let authAuthenticated = false;
  let remainingCitySummaryFailures = citySummaryFailureCount;
  let remainingPropertyFailures = propertyFailureCount;
  await page.route("https://unpkg.com/three-globe/example/img/**", (route) =>
    route.fulfill({ body: EMPTY_PNG, contentType: "image/png" }),
  );
  await page.route("https://images.example.test/**", (route) =>
    route.fulfill({ body: EMPTY_PNG, contentType: "image/png" }),
  );
  await page.route("https://www.openstreetmap.org/**", (route) =>
    route.fulfill({
      body: "<!doctype html><html><body><div>Street map</div></body></html>",
      contentType: "text/html",
    }),
  );
  await page.route((url) => url.pathname === "/runtime-config", (route) =>
    route.fulfill({ json: runtimeConfig }),
  );
  await page.route((url) => url.pathname === "/auth/session", (route) =>
    route.fulfill({
      json: {
        enabled: Boolean(runtimeConfig.auth?.enabled),
        authenticated: authAuthenticated,
        username: authAuthenticated ? runtimeConfig.auth?.usernameHint || "visitor" : null,
        guestClickLimit: runtimeConfig.auth?.guestClickLimit || 10,
        usernameHint: runtimeConfig.auth?.usernameHint || "visitor",
      },
    }),
  );
  await page.route((url) => url.pathname === "/auth/login", (route) => {
    const body = JSON.parse(route.request().postData() || "{}") as { username?: string; password?: string };
    if (body.username !== "visitor" || body.password !== "visitor123456") {
      return route.fulfill({ status: 401, json: { detail: "invalid credentials" } });
    }
    authAuthenticated = true;
    return route.fulfill({
      json: {
        enabled: true,
        authenticated: true,
        username: "visitor",
        guestClickLimit: runtimeConfig.auth?.guestClickLimit || 10,
        usernameHint: "visitor",
      },
    });
  });
  await page.route((url) => url.pathname === "/auth/logout", (route) => {
    authAuthenticated = false;
    return route.fulfill({
      json: {
        enabled: Boolean(runtimeConfig.auth?.enabled),
        authenticated: false,
        username: null,
        guestClickLimit: runtimeConfig.auth?.guestClickLimit || 10,
        usernameHint: runtimeConfig.auth?.usernameHint || "visitor",
      },
    });
  });
  await page.route((url) => url.pathname === "/updates", (route) =>
    route.fulfill({
      json: {
        locale: new URL(route.request().url()).searchParams.get("locale") || "en",
        count: productUpdates.length,
        updates: productUpdates,
      },
    }),
  );
  await page.route((url) => url.pathname === "/service-requests", (route) => {
    const body = JSON.parse(route.request().postData() || "{}") as Record<string, unknown>;
    serviceRequestSubmissions.push(body);
    return route.fulfill({
      status: 201,
      json: {
        request_id: "11111111-1111-4111-8111-111111111111",
        request_code: "SR-20260825-ABCD",
        status: "submitted",
        submitted_at: "2026-08-25T01:00:00+00:00",
        created: true,
      },
    });
  });
  await page.route((url) => url.pathname === "/rules/scenes", (route) =>
    route.fulfill({
      json: {
        scenes: {
          airport_terminal: {},
          convention_center: {},
          stadium: {},
          luxury_hotel_mice: {},
          mall_mixed_use: {},
          office_government: {},
        },
      },
    }),
  );
  await page.route((url) => url.pathname === "/rules/localization", async (route) => {
    const locale = new URL(route.request().url()).searchParams.get("locale") || "en";
    if (localizationDelayMs > 0 && locale !== "en") {
      await new Promise((resolve) => setTimeout(resolve, localizationDelayMs));
    }
    return route.fulfill({ json: mockLocalizationPayload(locale) });
  });
  await page.route((url) => url.pathname.startsWith("/map/satellite-tiles/"), async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3_000));
    return route.fulfill({ body: EMPTY_PNG, contentType: "image/png" });
  });
  await page.route((url) => url.pathname === "/scan-runs", (route) => {
    scanRunRequests.push(route.request().url());
    return route.fulfill({ json: [scanRun(RUN_ID, apiPackets.length), scanRun(SECOND_RUN_ID, apiPackets.length)] });
  });
  await page.route((url) => url.pathname === "/discovery/status", async (route) => {
    discoveryRequests.push(route.request().url());
    if (discoveryDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, discoveryDelayMs));
    }
    return route.fulfill({
      json: {
        task_backlog: { queued: 6, leased: 1, completed: 8 },
        progress_status: { active: 3, blocked_review: 1 },
        progress: discoveryProgressRows(apiPackets),
        pending_evidence_count: 2,
        raw_evidence_status: { new: 2, accepted: 6, review: 1 },
        latest_run: {
          id: "mock-discovery-run",
          worker_id: "firecrawl-worker-1",
          status: "completed",
          started_at: "2026-05-09T09:00:00Z",
          completed_at: "2026-05-09T09:05:00Z",
          searched_count: 12,
          fetched_count: 8,
          discovered_count: 7,
          new_count: 3,
          changed_count: 1,
          duplicate_count: 2,
          failed_count: 0,
          countries: COUNTRIES.map((row) => row.country),
          errors: [],
        },
      },
    });
  });
  await page.route((url) => url.pathname === "/raw-evidence", (route) => {
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({
      json: {
        count: packets.length,
        items: packets.map(rawEvidenceRow),
      },
    });
  });
  await page.route((url) => url.pathname === "/candidate-drafts", (route) => {
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({
      json: {
        count: packets.length,
        items: packets.map(candidateDraftRow),
      },
    });
  });
  await page.route((url) => url.pathname === "/review-queue", (route) => {
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({ json: packets.flatMap(reviewQueueRows) });
  });
  await page.route((url) => url.pathname === "/connectors/geocode", (route) => {
    geocodeRequests.push(route.request().url());
    const query = new URL(route.request().url()).searchParams.get("q");
    if (query !== "Setif, Algeria") {
      return route.fulfill({ json: { detail: "geocode result not found" }, status: 404 });
    }
    return route.fulfill({
      json: {
        latitude: CITY_ONLY_PLACE.lat,
        longitude: CITY_ONLY_PLACE.lng,
        geocode_precision: "city centroid",
        map_source: "Mock city centroid",
        city: CITY_ONLY_PLACE.city,
        country: CITY_ONLY_PLACE.country,
        display_name: "Setif, Algeria",
        boundingbox: null,
      },
    });
  });
  await page.route((url) => url.pathname === "/map/country-summary", async (route) => {
    dataRequests.push(route.request().url());
    if (countrySummaryDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, countrySummaryDelayMs));
    }
    return route.fulfill({ json: countrySummaries(apiPackets) });
  });
  await page.route((url) => url.pathname === "/map/city-summary", (route) => {
    dataRequests.push(route.request().url());
    if (remainingCitySummaryFailures > 0) {
      remainingCitySummaryFailures -= 1;
      return route.fulfill({ status: 503, json: { detail: "city summary unavailable" } });
    }
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({ json: { cities: citySummaries(packets) } });
  });
  await page.route((url) => url.pathname === "/map/properties", (route) => {
    dataRequests.push(route.request().url());
    const packets = filteredPackets(route.request().url(), apiPackets);
    return route.fulfill({
      json: {
        type: "FeatureCollection",
        features: packets.filter(isMapReadyPacket).map(mapFeature),
      },
    });
  });
  await page.route((url) => url.pathname.startsWith("/map/property-overlays/"), (route) => {
    dataRequests.push(route.request().url());
    const url = new URL(route.request().url());
    const layer = url.searchParams.get("layer") || "mobile_network";
    return route.fulfill({
      json: mockPropertyOverlay(layer),
    });
  });
  await page.route((url) => url.pathname === "/properties/search", (route) => {
    dataRequests.push(route.request().url());
    const query = new URL(route.request().url()).searchParams.get("q")?.trim() || "";
    const normalizedQuery = normalizeMockSearchText(query);
    const results = apiPackets.flatMap((packet) => {
      const aliases = packet.entity.aliases || [];
      const canonicalMatch = normalizeMockSearchText(packet.entity.property_name).includes(normalizedQuery);
      const matchedAlias = aliases.find((alias) => normalizeMockSearchText(alias).includes(normalizedQuery));
      if (!normalizedQuery || (!canonicalMatch && !matchedAlias)) {
        return [];
      }
      return [{
        property_id: packet.entity.property_id,
        property_name: packet.entity.property_name,
        matched_name: matchedAlias || packet.entity.property_name,
        match_type: matchedAlias ? "alias_exact" : "canonical_contains",
        country: packet.entity.country,
        city: packet.entity.city,
        scene_type: packet.entity.scene_type,
      }];
    }).slice(0, 20);
    return route.fulfill({
      json: { query, count: results.length, results },
    });
  });
  await page.route((url) => url.pathname === "/properties", (route) => {
    dataRequests.push(route.request().url());
    if (remainingPropertyFailures > 0) {
      remainingPropertyFailures -= 1;
      return route.fulfill({ status: 503, json: { detail: "property packets unavailable" } });
    }
    const locale = new URL(route.request().url()).searchParams.get("locale") || "en";
    const packets = filteredPackets(route.request().url(), apiPackets)
      .map((packet) => localizeMockPacket(packet, locale));
    const fulfill = () => route.fulfill({
      json: {
        candidate_count: packets.length,
        display_count: packets.length,
        packets,
      },
    });
    if (propertyDelayMs > 0) {
      return new Promise((resolve) => {
        setTimeout(() => {
          void fulfill().then(resolve);
        }, propertyDelayMs);
      });
    }
    return route.fulfill({
      json: {
        candidate_count: packets.length,
        display_count: packets.length,
        packets,
      },
    });
  });
  await page.route((url) => url.pathname === "/outputs/excel/country", (route) => {
    countryExportRequests.push(route.request().url());
    return route.fulfill({
      body: Buffer.from("mock country workbook"),
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers: {
        "Content-Disposition": "attachment; filename=isite_Algeria_standard_report_en_20260810T000000Z.xlsx",
      },
    });
  });
  await page.route((url) => url.pathname === "/outputs/excel" || url.pathname === "/outputs/ppt", async (route) => {
    exportRequests.push({
      path: new URL(route.request().url()).pathname,
      body: JSON.parse(route.request().postData() || "{}") as Record<string, unknown>,
    });
    return route.fulfill({
      status: 202,
      json: {
        artifact_type: new URL(route.request().url()).pathname.endsWith("/excel") ? "excel" : "ppt",
        path: "/tmp/mock-output",
        candidate_count: 1,
        filter_snapshot: {},
      },
    });
  });
  await page.route((url) => url.pathname === "/rag/index", (route) =>
    {
      ragRequests.push({
        path: new URL(route.request().url()).pathname,
        body: JSON.parse(route.request().postData() || "{}") as Record<string, unknown>,
      });
      return route.fulfill({
      status: 202,
      json: { indexed_documents: 1, indexed_chunks: 1, skipped_documents: 0 },
      });
    },
  );
  await page.route((url) => url.pathname === "/rag/query", (route) =>
    {
      ragRequests.push({
        path: new URL(route.request().url()).pathname,
        body: JSON.parse(route.request().postData() || "{}") as Record<string, unknown>,
      });
      return route.fulfill({
      json: {
        answer: "Mock evidence-backed answer for this property.",
        citations: [
          {
            chunk_id: "chunk-1",
            source_url: "https://example.com/mock-source",
            source_name: "Mock Airport Authority",
            source_tier: "Tier 1",
            source_date: "2026",
            fetched_at: "2026-05-09T00:00:00Z",
            raw_evidence_id: null,
            property_id: "mock-property-1",
            field_group: "annual_passenger_throughput",
            excerpt: "Mock airport handled 10,000,000 passengers.",
          },
        ],
        priority_recommendations: [
          {
            property_id: "mock-property-1",
            property_name: "Algiers airport terminal",
            country: "Algeria",
            scene_type: "airport_terminal",
            priority_band: "Evidence-backed Priority",
            evidence_status: "Supported",
            action_class: "Survey First",
            recommended_solution: "pRRU",
            rationale: "Mock rationale tied to public evidence.",
            review_next_actions: ["Mock review action"],
          },
        ],
        review_actions: ["Mock review action"],
      },
      });
    },
  );
}

function filteredPackets(url: string, packets: Array<ReturnType<typeof createPacket>>) {
  const params = new URL(url).searchParams;
  const country = params.get("country");
  const scene = params.get("scene_type");
  const evidence = params.get("evidence_status");
  const action = params.get("action_class");
  return packets.filter((packet) => {
    return (
      (!country || packet.entity.country === country) &&
      (!scene || packet.entity.scene_type === scene) &&
      (!evidence || packet.conclusion.evidence_status === evidence) &&
      (!action || packet.conclusion.action_class === action)
    );
  });
}

function mockLocalizationPayload(locale: string) {
  const isZh = locale === "zh";
  return {
    locale: isZh ? "zh" : "en",
    default_locale: "en",
    fallback_locale: "en",
    supported_locales: ["en", "zh"],
    labels: {
      fallback: {
        unknown: isZh ? "未知" : "Unknown",
        no_rows: isZh ? "无数据" : "No rows",
        no_primary_metric: isZh ? "无主指标" : "No primary metric",
        localization_pending: isZh ? "本地化待刷新" : "Localization pending",
      },
      scenes: Object.fromEntries(
        SCENES.map((scene) => [scene, isZh ? mockZhSceneLabel(scene) : mockEnglishSceneLabel(scene)]),
      ),
      enums: {
        Supported: isZh ? "已支撑" : "Supported",
        "City Core": isZh ? "城市核心" : "City Core",
        "Survey First": isZh ? "优先勘测" : "Survey First",
        Unknown: isZh ? "未知" : "Unknown",
      },
      ui: {
        opportunity_globe: isZh ? "机会地图" : "Opportunity Globe",
        loading: isZh ? "加载中" : "Loading",
        countries: isZh ? "国家" : "Countries",
        cities: isZh ? "城市" : "Cities",
        city_scope: isZh ? "城市范围" : "City scope",
        search_cities: isZh ? "搜索城市" : "City search",
        back_to_countries: isZh ? "返回国家列表" : "Back to countries",
        localities: isZh ? "辖区" : "Localities",
        no_cities_found: isZh ? "未找到城市" : "No cities found",
        candidates: isZh ? "候选点" : "Candidates",
        sources: isZh ? "证据" : "Evidence",
        review: isZh ? "复核" : "Review",
        action: isZh ? "动作" : "Action",
        primary_metric: isZh ? "一级主指标" : "Primary metric",
        network_complaints: isZh ? "网络投诉" : "Network complaints",
        property_complaint_signals: isZh ? "物业级公开信号" : "Property-level public signals",
        complaint_signal_note: isZh
          ? "仅展示公开网络投诉聚合，不代表室内 DAS 或建设状态证据。"
          : "Aggregated public network complaints only. This does not prove indoor DAS/build status.",
        complaint_empty: isZh
          ? "暂无合规的物业级网络投诉数据。"
          : "No compliant property-level network complaints are currently available.",
        valid_complaints: isZh ? "有效投诉" : "Valid complaints",
        complaint_sources: isZh ? "投诉来源" : "Complaint sources",
        complaint_pressure: isZh ? "投诉压力" : "Pressure",
        complaint_period: isZh ? "观察窗口" : "Observation window",
        complaint_latest: isZh ? "最近观测" : "Latest observation",
        complaint_categories: isZh ? "投诉分类" : "Complaint categories",
        complaint_period_days: isZh ? "{days} 天" : "{days} days",
        complaint_no_signal: isZh ? "无信号" : "No signal",
        complaint_weak_coverage: isZh ? "弱覆盖" : "Weak coverage",
        complaint_slow_data: isZh ? "数据慢" : "Slow data",
        complaint_dropped_call: isZh ? "掉话" : "Dropped calls",
        complaint_outage: isZh ? "网络中断" : "Network outage",
        complaint_insufficient: isZh ? "数据不足" : "Insufficient",
        high: isZh ? "高" : "High",
        moderate: isZh ? "中" : "Moderate",
        low: isZh ? "低" : "Low",
        scene: isZh ? "场景" : "Scene",
        localization_loading: isZh ? "正在加载本地化文案" : "Loading localized text",
        localization_loading_body: isZh ? "正在刷新国家、城市和物业点文案。" : "Refreshing country, city, and property copy.",
        guest_access: isZh ? "游客访问" : "Guest access",
        guest_clicks_remaining: isZh ? "剩余 {count} 次点击" : "{count} clicks left",
        sign_in: isZh ? "登录" : "Sign in",
        sign_out: isZh ? "退出登录" : "Sign out",
        signed_in_as: isZh ? "已登录：{username}" : "Signed in as {username}",
        login_required: isZh ? "需要登录" : "Login required",
        login_required_body: isZh
          ? "游客可点击 {limit} 次。请登录后继续使用。"
          : "Guest access includes {limit} clicks. Sign in to continue.",
        username: isZh ? "用户名" : "Username",
        password: isZh ? "密码" : "Password",
        invalid_credentials: isZh ? "用户名或密码错误" : "Invalid username or password",
        close: isZh ? "关闭" : "Close",
        request_short: isZh ? "提单" : "Request",
        request_title: isZh ? "提交需求" : "Submit a request",
        request_intro: isZh ? "告诉我们需要扩充、改进或交付的内容。" : "Tell us what should be expanded or delivered.",
        request_type: isZh ? "提单类型" : "Request type",
        request_scan: isZh ? "增强看网" : "Expand scan",
        request_feature: isZh ? "新功能建议" : "Feature idea",
        request_ppt: isZh ? "PPT 报告" : "PPT report",
        request_country: isZh ? "国家" : "Country",
        request_country_placeholder: isZh ? "搜索或输入国家" : "Search or enter a country",
        request_country_placeholder_existing: isZh ? "搜索已有国家" : "Search available countries",
        request_use_custom_country: isZh ? "使用“{value}”作为自定义国家" : "Use “{value}” as a custom country",
        request_city_scope: isZh ? "城市范围" : "City scope",
        request_single_city: isZh ? "单一城市" : "Single city",
        request_national_main_cities: isZh ? "全国主要城市" : "National main cities",
        request_city: isZh ? "城市" : "City",
        request_city_placeholder: isZh ? "搜索或输入城市" : "Search or enter a city",
        request_use_custom_city: isZh ? "使用“{value}”作为自定义城市" : "Use “{value}” as a custom city",
        request_custom_location_status: isZh ? "自定义条目 · 提交后核验" : "Custom entry · verified after submission",
        request_no_matching_locations: isZh ? "没有匹配地点" : "No matching locations",
        request_location_required: isZh ? "请选择或输入必要的地点。" : "Choose or enter the required location.",
        request_scene: isZh ? "场景" : "Scene",
        request_scenes: isZh ? "场景" : "Scenes",
        request_scenes_selected: isZh ? "已选 {count} 个" : "{count} selected",
        request_clear_scenes: isZh ? "清空" : "Clear",
        request_scene_required: isZh ? "请至少选择一个场景。" : "Select at least one scene.",
        request_target_count: isZh ? "目标新增合格物业数" : "Target new qualified properties",
        request_target_total_note: isZh ? "该目标为所有已选场景的合计。" : "One combined target across all selected scenes.",
        request_decrease_target: isZh ? "减少目标数" : "Decrease target",
        request_increase_target: isZh ? "增加目标数" : "Increase target",
        request_contact_email: isZh ? "联系邮箱" : "Contact email",
        request_submit: isZh ? "提交需求" : "Submit request",
        request_submitted: isZh ? "提单已提交" : "Request submitted",
        updates_short: isZh ? "更新" : "Updates",
        updates_title: isZh ? "更新通知" : "Product updates",
        updates_intro: isZh ? "最近上线的看网数据和功能更新。" : "Recently published scan and feature improvements.",
        updates_empty: isZh ? "暂无已发布更新。" : "No published updates yet.",
        update_scan: isZh ? "看网更新" : "Scan update",
        update_feature: isZh ? "功能更新" : "Feature update",
      },
    },
    fallback_labels: {},
  };
}

function localizeMockPacket(packet: ReturnType<typeof createPacket>, locale: string) {
  const cloned = JSON.parse(JSON.stringify(packet)) as ReturnType<typeof createPacket> & {
    localized?: Record<string, any>;
  };
  const evidenceValue = mockPrimaryMetricEvidenceValue(cloned);
  const isZh = locale === "zh";
  if (!isZh) {
    cloned.localized = {
      primary_metric: {
        display_text: evidenceValue,
        localization_status: "success",
        source_locale: "en",
      },
      conclusion: {
        reason_to_recommend: cloned.conclusion.reason_to_recommend,
        next_action: cloned.conclusion.next_action,
      },
    };
    return cloned;
  }
  cloned.localized = {
    entity: {
      scene_label: mockZhSceneLabel(cloned.entity.scene_type),
    },
    primary_metric: {
      display_text: `中文主指标：${evidenceValue}`,
      localization_status: "success",
      source_locale: "en",
    },
    conclusion: {
      evidence_status_label: "已支撑",
      value_class_label: "城市核心",
      action_class_label: "优先勘测",
      recommended_solution_label: "优先勘测",
      reason_to_recommend: "中文推荐理由：公开证据支持该物业为高价值机会点。",
      next_action: "中文下一步：核验坐标并补查室分建设状态。",
    },
    build_status: {
      indoor_rat_label: "未知",
    },
  };
  return cloned;
}

function mockPrimaryMetricEvidenceValue(packet: ReturnType<typeof createPacket>): string {
  const objectiveEvidence = packet.evidence.find((item) =>
    item.indicator_name !== "gateway_role"
    && item.indicator_name !== "hub_role"
    && item.indicator_name !== "landmark_role",
  );
  return objectiveEvidence?.field_value || packet.evidence[0]?.field_value || "";
}

function mockEnglishSceneLabel(scene: string) {
  const labels: Record<string, string> = {
    airport_terminal: "Airport",
    convention_center: "Convention",
    stadium: "Stadium",
    mall_mixed_use: "Mall / Mixed-use",
    luxury_hotel_mice: "Hotel / MICE",
    mosque: "Mosque",
  };
  return labels[scene] || scene.replaceAll("_", " ");
}

function mockZhSceneLabel(scene: string) {
  const labels: Record<string, string> = {
    airport_terminal: "机场",
    convention_center: "会展中心",
    stadium: "体育场",
    mall_mixed_use: "大型商超",
    luxury_hotel_mice: "奢华酒店",
    mosque: "清真寺",
  };
  return labels[scene] || scene.replaceAll("_", " ");
}

function scanRun(runId: string, candidateCount = PACKETS.length) {
  return {
    run_id: runId,
    status: "completed",
    created_at: "2026-05-09T09:00:00Z",
    scope: { countries: COUNTRIES.map((row) => row.country) },
    candidate_count: candidateCount,
    review_count: candidateCount,
    storage_mode: "mock",
  };
}

function createPacket(
  place: { country: string; city: string; lat: number; lng: number },
  scene: string,
  index: number,
  options: { coordinateStatus?: string; heroImage?: MockHeroImage | null; aliases?: string[] } = {},
) {
  const id = `${place.country.toLowerCase().replaceAll(" ", "-")}-${place.city.toLowerCase().replaceAll(" ", "-")}-${scene}`;
  const primaryEvidence = mockPrimaryEvidence(scene, place, index);
  return {
    entity: {
      property_id: id,
      country: place.country,
      city: place.city,
      property_name: `${place.city} ${scene.replaceAll("_", " ")}`,
      aliases: options.aliases || [],
      scene_type: scene,
      latitude: place.lat + index * 0.018,
      longitude: place.lng + index * 0.021,
      geocode_precision: "venue centroid",
      map_source: "Mock map source",
      map_source_date: "2026-05-09",
      google_maps_link: `https://www.google.com/maps/search/?api=1&query=${place.lat},${place.lng}`,
      coordinate_status: options.coordinateStatus || "Map Ready",
      hero_image: options.heroImage ?? null,
    },
    scene: {
      annual_visits_est: 1_000_000 + index * 100_000,
      proxy_level: "Supported",
      area_metric_name: "mock metric",
      proxy_basis: "mock evidence basis",
    },
    evidence: [
      {
        field_group: primaryEvidence.indicatorName,
        field_value: primaryEvidence.fieldValue,
        indicator_name: primaryEvidence.indicatorName,
        source_name: "Mock source",
        source_url: "https://example.com/mock-source",
        source_date: "2026-05-09",
        source_tier: "Tier 1",
        evidence_type: "official",
        cross_check_status: "Cross-checked",
      },
    ],
    build_status: {
      indoor_system_presence: "Unknown",
      indoor_system_type: "Unknown",
      indoor_rat: "Unknown",
      build_evidence_status: "Insufficient",
    },
    demand: {
      busy_hour_traffic_gb: 12 + index,
      busy_hour_bandwidth_mbps: 260 + index * 20,
      busy_hour_users: 600 + index * 50,
    },
    inference: [
      {
        inferred_field: "busy hour users",
        inferred_value: `${600 + index * 50}`,
        inference_basis: "mock traffic profile",
        inference_chain: "mock visits -> busy users -> traffic",
        inference_confidence: "Medium",
      },
    ],
    conclusion: {
      evidence_status: "Supported",
      value_class: "City Core",
      action_class: "Survey First",
      recommended_solution: "Survey First",
      reason_to_recommend: "Mock evidence supports a high-value opportunity.",
      next_action: "Validate coordinate and indoor build status.",
    },
    review_queue: [
      {
        reason: "Indoor build status unknown",
        next_action: "Check operator indoor system announcement.",
        status: "open",
      },
    ],
  };
}

function mockPrimaryEvidence(
  scene: string,
  place: { city: string },
  index: number,
) {
  const cityScene = `${place.city} ${scene.replaceAll("_", " ")}`;
  switch (scene) {
    case "airport_terminal":
      return {
        indicatorName: "annual_passenger_throughput",
        fieldValue: `${cityScene} handled ${formatMockMetric(12_000_000 + index * 1_000_000)} passengers`,
      };
    case "convention_center":
      return {
        indicatorName: "exhibition_area",
        fieldValue: `${cityScene} has ${formatMockMetric(25_000 + index * 2_000)} sqm exhibition area`,
      };
    case "stadium":
      return {
        indicatorName: "seat_count",
        fieldValue: `${cityScene} has ${formatMockMetric(40_000 + index * 5_000)} seats`,
      };
    case "mall_mixed_use":
      return {
        indicatorName: "gla",
        fieldValue: `${cityScene} has ${formatMockMetric(80_000 + index * 8_000)} sqm GLA`,
      };
    case "luxury_hotel_mice":
      return {
        indicatorName: "room_count",
        fieldValue: `${cityScene} has ${formatMockMetric(120 + index * 20)} rooms`,
      };
    case "office_government":
      return {
        indicatorName: "office_gfa",
        fieldValue: `${cityScene} has ${formatMockMetric(60_000 + index * 6_000)} sqm office GFA`,
      };
    case "hospital":
      return {
        indicatorName: "beds",
        fieldValue: `${cityScene} has ${formatMockMetric(500 + index * 50)} beds`,
      };
    case "university":
      return {
        indicatorName: "enrollment",
        fieldValue: `${cityScene} has ${formatMockMetric(15_000 + index * 1_000)} students`,
      };
    case "transport_hub":
      return {
        indicatorName: "daily_ridership",
        fieldValue: `${cityScene} serves ${formatMockMetric(100_000 + index * 10_000)} daily riders`,
      };
    case "cruise_port":
      return {
        indicatorName: "annual_passenger_throughput",
        fieldValue: `${cityScene} handled ${formatMockMetric(500_000 + index * 50_000)} passengers`,
      };
    case "mosque":
      return {
        indicatorName: "mosque_area",
        fieldValue: `${cityScene} has ${formatMockMetric(22_000 + index * 2_000)} sqm mosque area`,
      };
    default:
      return {
        indicatorName: "annual_visits",
        fieldValue: `${cityScene} has ${formatMockMetric(1_000_000 + index * 100_000)} annual visits`,
      };
  }
}

function formatMockMetric(value: number) {
  return value.toLocaleString("en-US");
}

function withPrimaryEvidence(
  packet: ReturnType<typeof createPacket>,
  indicatorName: string,
  fieldValue: string,
) {
  return withEvidence(packet, [
    {
      field_group: indicatorName,
      indicator_name: indicatorName,
      field_value: fieldValue,
    },
  ]);
}

function withEvidence(
  packet: ReturnType<typeof createPacket>,
  evidenceRows: Array<{
    field_group: string;
    indicator_name: string;
    field_value: string;
  }>,
) {
  const template = packet.evidence[0];
  return {
    ...packet,
    evidence: evidenceRows.map((row) => ({
      ...template,
      ...row,
    })),
  };
}

function isMapReadyPacket(packet: ReturnType<typeof createPacket>) {
  return packet.entity.coordinate_status === "Map Ready";
}

function mapFeature(packet: ReturnType<typeof createPacket>) {
  return {
    type: "Feature",
    geometry: {
      type: "Point",
      coordinates: [packet.entity.longitude, packet.entity.latitude],
    },
    properties: {
      property_id: packet.entity.property_id,
      property_name: packet.entity.property_name,
      country: packet.entity.country,
      city: packet.entity.city,
      scene_type: packet.entity.scene_type,
      evidence_status: packet.conclusion.evidence_status,
      value_class: packet.conclusion.value_class,
      action_class: packet.conclusion.action_class,
      recommended_solution: packet.conclusion.recommended_solution,
      main_metric_text: packet.evidence[0].field_value,
      annual_visits_est: packet.scene.annual_visits_est,
      busy_hour_traffic_gb: packet.demand.busy_hour_traffic_gb,
      review_count: packet.review_queue.length,
      source_count: packet.evidence.length,
      indoor_system_presence: packet.build_status.indoor_system_presence,
      indoor_rat: packet.build_status.indoor_rat,
      proxy_level: packet.scene.proxy_level,
      last_scan_at: "2026-05-09T09:00:00Z",
      google_maps_link: packet.entity.google_maps_link,
      geocode_precision: packet.entity.geocode_precision,
      map_source: packet.entity.map_source,
      coordinate_status: packet.entity.coordinate_status,
      hero_image_url: packet.entity.hero_image?.url ?? null,
      hero_image_alt: packet.entity.hero_image?.alt_text ?? null,
      hero_image_source_name: packet.entity.hero_image?.source_name ?? null,
    },
  };
}

function mockPropertyOverlay(layer: string) {
  const overlayProperties = {
    cell_id: `${layer}-fixture`,
    score: layer === "footfall" ? 0.86 : 0.72,
    metric_value: layer === "footfall" ? 12500 : 72,
    metric_label: layer === "footfall" ? "Visits" : "Mobile download Mbps",
    download_mbps: 72,
    upload_mbps: 18,
    latency_ms: 31,
    loaded_latency_down_ms: 96,
    tests: 24,
    devices: 9,
    confidence: "high",
    performance_class: "good",
    source_name: layer === "footfall" ? "Public footfall source" : "Ookla Open Data",
    period: "2026 Q1",
    distance_to_property_m: 420,
  };
  const tileFeature = {
    type: "Feature",
    geometry: {
      type: "Polygon",
      coordinates: [[
        [-1.05, 35.3],
        [4.0, 35.3],
        [4.0, 37.0],
        [-1.05, 37.0],
        [-1.05, 35.3],
      ]],
    },
    properties: {
      ...overlayProperties,
      feature_kind: layer === "footfall" ? "footfall_cell" : "tile",
    },
  };
  const payload = {
    type: "FeatureCollection",
    layer,
    provider: layer === "footfall" ? "public_open_data" : "ookla_open_data",
    status: "ready",
    message: layer === "footfall"
      ? "1 public footfall observation within 5 km."
      : "Ookla mobile network experience tile ready.",
    features: layer === "mobile_network" ? [
      tileFeature,
      {
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [3.215, 36.691],
        },
        properties: {
          ...overlayProperties,
          feature_kind: "tile_center",
          center_label: "Tile center",
        },
      },
    ] : [
      tileFeature,
      {
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [3.215, 36.691],
        },
        properties: {
          ...overlayProperties,
          feature_kind: "footfall_center",
          center_label: "Observation center",
        },
      },
    ],
  };
  return layer === "mobile_network"
    ? {
        ...payload,
        proxy_note: "Ookla mobile z16 surrounding tile proxy; not indoor DAS/build evidence.",
      }
    : payload;
}

function rawEvidenceRow(packet: ReturnType<typeof createPacket>, index: number) {
  return {
    id: `raw-${packet.entity.property_id}`,
    region: "mock-region",
    country: packet.entity.country,
    city: packet.entity.city,
    property_name: packet.entity.property_name,
    scene_type: packet.entity.scene_type,
    source_type: index % 2 === 0 ? "firecrawl_search" : "operator_venue",
    source_url: packet.evidence[0].source_url,
    source_name: packet.evidence[0].source_name,
    source_tier: packet.evidence[0].source_tier,
    source_date: packet.evidence[0].source_date,
    field_group: packet.evidence[0].field_group,
    indicator_name: packet.evidence[0].indicator_name,
    field_value: packet.evidence[0].field_value,
    status: index % 3 === 0 ? "new" : "accepted",
    curation_needed: index % 3 === 0,
    curation_run_id: "mock-curation-run",
    curated_at: "2026-05-09T09:10:00Z",
    created_at: "2026-05-09T09:00:00Z",
  };
}

function candidateDraftRow(packet: ReturnType<typeof createPacket>, index: number) {
  return {
    id: `draft-${packet.entity.property_id}`,
    curation_run_id: "mock-curation-run",
    raw_evidence_ids: [`raw-${packet.entity.property_id}`],
    country: packet.entity.country,
    city: packet.entity.city,
    property_name: packet.entity.property_name,
    scene_type: packet.entity.scene_type,
    source_type: index % 2 === 0 ? "firecrawl_search" : "operator_venue",
    status: index % 4 === 0 ? "review" : "accepted",
    issues: index % 4 === 0 ? ["Check coordinate precision"] : [],
    candidate_payload: {
      property_name: packet.entity.property_name,
      evidence: packet.evidence,
    },
    created_at: "2026-05-09T09:12:00Z",
  };
}

function reviewQueueRows(packet: ReturnType<typeof createPacket>) {
  return packet.review_queue.map((item) => ({
    property_id: packet.entity.property_id,
    property_name: packet.entity.property_name,
    country: packet.entity.country,
    city: packet.entity.city,
    scene_type: packet.entity.scene_type,
    candidate_quality_status: "accepted",
    visibility: "visible",
    quality_issues: [],
    reason: item.reason,
    next_action: item.next_action,
    status: item.status,
    review_type: "build_status",
    severity: "medium",
    source_url: packet.evidence[0].source_url,
  }));
}

function discoveryProgressRows(packets: Array<ReturnType<typeof createPacket>>) {
  const groups = new Map<string, ReturnType<typeof createPacket>>();
  packets.forEach((packet) => {
    const key = `${packet.entity.country}-${packet.entity.scene_type}`;
    if (!groups.has(key)) {
      groups.set(key, packet);
    }
  });
  return Array.from(groups.values()).slice(0, 4).map((packet, index) => ({
    id: `progress-${packet.entity.country}-${packet.entity.scene_type}`,
    region: "mock-region",
    country: packet.entity.country,
    scene_type: packet.entity.scene_type,
    source_type: index % 2 === 0 ? "firecrawl_search" : "operator_venue",
    status: index === 3 ? "blocked_review" : "active",
    cycle_number: index + 1,
    no_new_cycles: index === 3 ? 1 : 0,
    accepted_new_total: 2 + index,
    accepted_new_last_cycle: index,
    draft_review_total: index === 3 ? 2 : 0,
    last_completed_at: "2026-05-09T09:15:00Z",
    exhausted_at: null,
    last_error: index === 3 ? "coordinate_review" : null,
    updated_at: "2026-05-09T09:15:00Z",
  }));
}

function countrySummaries(packets: Array<ReturnType<typeof createPacket>>) {
  const countries = Array.from(new Set(packets.map((packet) => packet.entity.country))).sort();
  return countries.map((country) => {
    const rows = packets.filter((packet) => packet.entity.country === country);
    return {
      country,
      candidate_count: rows.length,
      map_point_count: rows.filter(isMapReadyPacket).length,
      coordinate_review_count: rows.filter((packet) => packet.entity.coordinate_status === "Review Required").length,
      review_count: rows.reduce((total, packet) => total + packet.review_queue.length, 0),
      source_count: rows.reduce((total, packet) => total + packet.evidence.length, 0),
      scenes: Object.fromEntries(SCENES.map((scene) => [scene, rows.filter((packet) => packet.entity.scene_type === scene).length])),
      scan_maturity: {
        level: "seed_scan",
        basis: "active_coverage",
        scene_count: new Set(rows.map((packet) => packet.entity.scene_type)).size,
        evidence_per_candidate: 1,
        progress_group_count: 0,
        exhausted_group_count: 0,
        tracked_rounds: 0,
      },
    };
  });
}

function citySummaries(packets: Array<ReturnType<typeof createPacket>>) {
  const cities = new Map<string, {
    country: string;
    city: string;
    candidate_count: number;
    map_point_count: number;
    review_count: number;
    source_count: number;
    scenes: Record<string, number>;
    property_ids: string[];
    map_lat_total: number;
    map_lng_total: number;
    map_count: number;
    all_lat_total: number;
    all_lng_total: number;
    all_count: number;
  }>();
  packets.forEach((packet) => {
    const key = `${packet.entity.country.toLowerCase()}::${packet.entity.city.toLowerCase()}`;
    const row = cities.get(key) || {
      country: packet.entity.country,
      city: packet.entity.city,
      candidate_count: 0,
      map_point_count: 0,
      review_count: 0,
      source_count: 0,
      scenes: {},
      property_ids: [],
      map_lat_total: 0,
      map_lng_total: 0,
      map_count: 0,
      all_lat_total: 0,
      all_lng_total: 0,
      all_count: 0,
    };
    row.candidate_count += 1;
    row.review_count += packet.review_queue.length;
    row.scenes[packet.entity.scene_type] = (row.scenes[packet.entity.scene_type] || 0) + 1;
    row.property_ids.push(packet.entity.property_id);
    row.source_count += Math.max(1, packet.evidence.length);
    row.all_lat_total += packet.entity.latitude;
    row.all_lng_total += packet.entity.longitude;
    row.all_count += 1;
    if (isMapReadyPacket(packet)) {
      row.map_point_count += 1;
      row.map_lat_total += packet.entity.latitude;
      row.map_lng_total += packet.entity.longitude;
      row.map_count += 1;
    }
    cities.set(key, row);
  });
  return Array.from(cities.values())
    .map(({ map_lat_total, map_lng_total, map_count, all_lat_total, all_lng_total, all_count, ...row }) => ({
      ...row,
      lat: map_count > 0 ? map_lat_total / map_count : all_lat_total / all_count,
      lng: map_count > 0 ? map_lng_total / map_count : all_lng_total / all_count,
      position_source: map_count > 0 ? "map_ready_average" : "property_average",
    }))
    .sort((left, right) => right.candidate_count - left.candidate_count || left.city.localeCompare(right.city));
}

function expectDataRequestsUseGlobalCandidatePool(dataRequests: string[]) {
  const requestedPaths = dataRequests.map((url) => new URL(url).pathname);
  expect(requestedPaths).toContain("/map/country-summary");
  const unscopedCandidatePayloads = dataRequests.filter((url) => {
    const request = new URL(url);
    return ["/map/city-summary", "/map/properties", "/properties"].includes(request.pathname)
      && !request.searchParams.has("country");
  });
  expect(unscopedCandidatePayloads).toEqual([]);
  expect(dataRequests.every((url) => !new URL(url).searchParams.has("scan_run_id"))).toBe(true);
}

function isIgnorableConsoleError(line: string): boolean {
  return (
    line.includes("favicon") ||
    line.includes("Failed to load resource: the server responded with a status of 404")
  );
}
