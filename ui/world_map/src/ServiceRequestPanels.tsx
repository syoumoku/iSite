import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Bell,
  Check,
  CheckCircle2,
  ChevronDown,
  ClipboardPlus,
  Lightbulb,
  LoaderCircle,
  MapPinned,
  Minus,
  Plus,
  Presentation,
  Search,
  Send,
  X,
} from "lucide-react";

type LocalizationPayload = {
  locale: string;
  labels: Record<string, any>;
  fallback_labels?: Record<string, any>;
};

export type ProductUpdate = {
  id: string;
  category: "scan" | "feature";
  title: string;
  summary: string;
  country?: string | null;
  city?: string | null;
  scene_type?: string | null;
  scene_label?: string | null;
  scene_types?: string[];
  scene_labels?: string[];
  actual_new_count?: number | null;
  published_at: string;
};

type RequestType = "scan_enhancement" | "feature_request" | "ppt_report";
type LocationInputMode = "catalog" | "custom";

export function ServiceRequestDrawer({
  open,
  locale,
  localization,
  countries,
  initialCountry,
  onClose,
}: {
  open: boolean;
  locale: string;
  localization: LocalizationPayload;
  countries: string[];
  initialCountry: string;
  onClose: () => void;
}) {
  const [requestType, setRequestType] = useState<RequestType>("scan_enhancement");
  const [email, setEmail] = useState("");
  const [country, setCountry] = useState(initialCountry || countries[0] || "");
  const [countryInputMode, setCountryInputMode] = useState<LocationInputMode>("catalog");
  const [pptCountry, setPptCountry] = useState(initialCountry || countries[0] || "");
  const [cityScope, setCityScope] = useState<"single_city" | "national_main_cities">("single_city");
  const [city, setCity] = useState("");
  const [cityInputMode, setCityInputMode] = useState<LocationInputMode>("catalog");
  const [cities, setCities] = useState<string[]>([]);
  const [citiesLoading, setCitiesLoading] = useState(false);
  const [selectedSceneTypes, setSelectedSceneTypes] = useState<string[]>([]);
  const [sceneTypes, setSceneTypes] = useState<string[]>([]);
  const [targetCount, setTargetCount] = useState(10);
  const [featureTitle, setFeatureTitle] = useState("");
  const [currentWorkflow, setCurrentWorkflow] = useState("");
  const [requestedFlow, setRequestedFlow] = useState("");
  const [expectedOutcome, setExpectedOutcome] = useState("");
  const [reportLocale, setReportLocale] = useState<"en" | "zh">(locale === "zh" ? "zh" : "en");
  const [clientRequestId, setClientRequestId] = useState(() => crypto.randomUUID());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [submittedCode, setSubmittedCode] = useState("");

  const sortedCountries = useMemo(
    () => [...new Set(countries.filter(Boolean))].sort((left, right) => left.localeCompare(right)),
    [countries],
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    const preferred = initialCountry || sortedCountries[0] || "";
    if (!country) {
      setCountry(preferred);
      setCountryInputMode("catalog");
    }
    if (!pptCountry) {
      setPptCountry(preferred);
    }
  }, [country, initialCountry, open, pptCountry, sortedCountries]);

  useEffect(() => {
    if (open && initialCountry) {
      setCountry(initialCountry);
      setCountryInputMode("catalog");
      setPptCountry(initialCountry);
      setCity("");
      setCityInputMode("catalog");
    }
  }, [initialCountry, open]);

  useEffect(() => {
    setReportLocale(locale === "zh" ? "zh" : "en");
  }, [locale]);

  useEffect(() => {
    if (!open || sceneTypes.length > 0) {
      return;
    }
    const controller = new AbortController();
    void fetch("/rules/scenes", { signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("scene rules unavailable")))
      .then((payload) => setSceneTypes(Object.keys(payload?.scenes || {})))
      .catch((fetchError) => {
        if (fetchError?.name !== "AbortError") {
          setError(label(localization, "ui.request_load_options_error", "Unable to load request options."));
        }
      });
    return () => controller.abort();
  }, [localization, open, sceneTypes.length]);

  useEffect(() => {
    if (
      !open
      || requestType !== "scan_enhancement"
      || cityScope !== "single_city"
      || !country
      || countryInputMode === "custom"
    ) {
      setCities([]);
      setCitiesLoading(false);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({ country });
    setCitiesLoading(true);
    void fetch(`/map/city-summary?${params.toString()}`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("cities unavailable")))
      .then((rows) => {
        const values = [...new Set(
          (Array.isArray(rows) ? rows : []).map((row) => String(row.city || "")).filter(Boolean),
        )];
        setCities(values);
      })
      .catch((fetchError) => {
        if (fetchError?.name !== "AbortError") {
          setCities([]);
          setCity("");
        }
      })
      .finally(() => setCitiesLoading(false));
    return () => controller.abort();
  }, [cityScope, country, countryInputMode, open, requestType]);

  if (!open) {
    return null;
  }

  const resetForAnother = () => {
    setSubmittedCode("");
    setError("");
    setClientRequestId(crypto.randomUUID());
  };

  const selectCountry = (value: string, mode: LocationInputMode) => {
    setCountry(value);
    setCountryInputMode(mode);
    setCity("");
    setCityInputMode("catalog");
  };

  const setScope = (value: "single_city" | "national_main_cities") => {
    setCityScope(value);
    if (value === "national_main_cities") {
      setCity("");
      setCityInputMode("catalog");
    }
  };

  const toggleScene = (sceneType: string) => {
    setSelectedSceneTypes((current) => current.includes(sceneType)
      ? current.filter((item) => item !== sceneType)
      : [...current, sceneType]);
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) {
      return;
    }
    if (requestType === "scan_enhancement") {
      if (!country.trim() || (cityScope === "single_city" && !city.trim())) {
        setError(label(localization, "ui.request_location_required", "Choose or enter the required location."));
        return;
      }
      if (selectedSceneTypes.length === 0) {
        setError(label(localization, "ui.request_scene_required", "Select at least one scene."));
        return;
      }
    }
    setSubmitting(true);
    setError("");
    const common = {
      request_type: requestType,
      contact_email: email.trim(),
      locale: locale === "zh" ? "zh" : "en",
      client_request_id: clientRequestId,
    };
    const payload = requestType === "scan_enhancement"
      ? {
          ...common,
          country: country.trim(),
          country_input_mode: countryInputMode,
          city_scope: cityScope,
          city: cityScope === "single_city" ? city.trim() : null,
          city_input_mode: cityScope === "single_city" ? cityInputMode : "not_applicable",
          scene_types: selectedSceneTypes,
          target_new_qualified_properties: targetCount,
        }
      : requestType === "feature_request"
        ? {
            ...common,
            title: featureTitle,
            current_workflow: currentWorkflow,
            requested_flow: requestedFlow,
            expected_outcome: expectedOutcome,
          }
        : { ...common, country: pptCountry, report_locale: reportLocale };
    try {
      const response = await fetch("/service-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(readApiDetail(result) || `${response.status} ${response.statusText}`);
      }
      setSubmittedCode(String(result.request_code || ""));
    } catch (submitError) {
      setError(submitError instanceof Error
        ? submitError.message
        : label(localization, "ui.request_submit_error", "Request submission failed."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="service-drawer-backdrop" data-guest-click-exempt="true" onMouseDown={onClose}>
      <aside
        className="service-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={label(localization, "ui.request_title", "Submit a request")}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="service-drawer-head">
          <div>
            <ClipboardPlus size={20} aria-hidden="true" />
            <div>
              <h2>{label(localization, "ui.request_title", "Submit a request")}</h2>
              <p>{label(localization, "ui.request_intro", "Tell us what should be expanded or delivered.")}</p>
            </div>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label={label(localization, "ui.close", "Close")}>
            <X size={18} />
          </button>
        </header>

        {submittedCode ? (
          <div className="request-success" data-testid="request-success">
            <CheckCircle2 size={34} aria-hidden="true" />
            <h3>{label(localization, "ui.request_submitted", "Request submitted")}</h3>
            <p>{label(localization, "ui.request_submitted_body", "Keep this request code for reference.")}</p>
            <strong>{submittedCode}</strong>
            <div className="service-drawer-actions">
              <button type="button" className="secondary" onClick={resetForAnother}>{label(localization, "ui.request_submit_another", "Submit another")}</button>
              <button type="button" className="primary" onClick={onClose}>{label(localization, "ui.close", "Close")}</button>
            </div>
          </div>
        ) : (
          <form className="service-request-form" onSubmit={submit}>
            <div className="request-form-scroll">
              <div className="request-type-tabs" role="tablist" aria-label={label(localization, "ui.request_type", "Request type")}>
                <RequestTypeButton active={requestType === "scan_enhancement"} icon={<MapPinned size={16} />} label={label(localization, "ui.request_scan", "Expand scan")} onClick={() => setRequestType("scan_enhancement")} />
                <RequestTypeButton active={requestType === "feature_request"} icon={<Lightbulb size={16} />} label={label(localization, "ui.request_feature", "Feature idea")} onClick={() => setRequestType("feature_request")} />
                <RequestTypeButton active={requestType === "ppt_report"} icon={<Presentation size={16} />} label={label(localization, "ui.request_ppt", "PPT report")} onClick={() => setRequestType("ppt_report")} />
              </div>

              {requestType === "scan_enhancement" && (
                <div className="request-fields" data-testid="expand-scan-fields">
                  <SearchCombobox
                    labelText={label(localization, "ui.request_country", "Country")}
                    placeholder={label(localization, "ui.request_country_placeholder", "Search or enter a country")}
                    options={sortedCountries}
                    value={country}
                    mode={countryInputMode}
                    allowCustom
                    customAction={(value) => formatLabel(localization, "ui.request_use_custom_country", "Use “{value}” as a custom country", value)}
                    customStatus={label(localization, "ui.request_custom_location_status", "Custom entry · verified after submission")}
                    emptyText={label(localization, "ui.request_no_matching_locations", "No matching locations")}
                    loadingText={label(localization, "ui.loading", "Loading")}
                    onChange={selectCountry}
                  />

                  <SegmentedControl
                    legend={label(localization, "ui.request_city_scope", "City scope")}
                    name="city-scope"
                    value={cityScope}
                    options={[
                      { value: "single_city", label: label(localization, "ui.request_single_city", "Single city") },
                      { value: "national_main_cities", label: label(localization, "ui.request_national_main_cities", "National main cities") },
                    ]}
                    onChange={(value) => setScope(value as typeof cityScope)}
                  />

                  {cityScope === "single_city" && (
                    <SearchCombobox
                      labelText={label(localization, "ui.request_city", "City")}
                      placeholder={label(localization, "ui.request_city_placeholder", "Search or enter a city")}
                      options={cities}
                      value={city}
                      mode={cityInputMode}
                      loading={citiesLoading}
                      allowCustom
                      customAction={(value) => formatLabel(localization, "ui.request_use_custom_city", "Use “{value}” as a custom city", value)}
                      customStatus={label(localization, "ui.request_custom_location_status", "Custom entry · verified after submission")}
                      emptyText={label(localization, "ui.request_no_matching_locations", "No matching locations")}
                      loadingText={label(localization, "ui.loading", "Loading")}
                      onChange={(value, mode) => {
                        setCity(value);
                        setCityInputMode(mode);
                      }}
                    />
                  )}

                  <fieldset className="scene-selector">
                    <legend>{label(localization, "ui.request_scenes", "Scenes")}</legend>
                    <div className="field-heading">
                      <span>{formatCountLabel(
                        label(localization, "ui.request_scenes_selected", "{count} selected"),
                        selectedSceneTypes.length,
                      )}</span>
                      {selectedSceneTypes.length > 0 && (
                        <button type="button" onClick={() => setSelectedSceneTypes([])}>
                          {label(localization, "ui.request_clear_scenes", "Clear")}
                        </button>
                      )}
                    </div>
                    <div className="scene-checkbox-grid">
                      {sceneTypes.map((sceneType) => {
                        const checked = selectedSceneTypes.includes(sceneType);
                        return (
                          <label key={sceneType} className={checked ? "checked" : ""}>
                            <input type="checkbox" checked={checked} onChange={() => toggleScene(sceneType)} />
                            <span className="scene-check" aria-hidden="true">{checked && <Check size={14} />}</span>
                            <span>{String(localization.labels?.scenes?.[sceneType] || sceneType.replaceAll("_", " "))}</span>
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>

                  <TargetStepper
                    labelText={label(localization, "ui.request_target_count", "Target new qualified properties")}
                    note={label(localization, "ui.request_target_total_note", "One combined target across all selected scenes. Only new active properties count.")}
                    value={targetCount}
                    decrementLabel={label(localization, "ui.request_decrease_target", "Decrease target")}
                    incrementLabel={label(localization, "ui.request_increase_target", "Increase target")}
                    onChange={setTargetCount}
                  />
                </div>
              )}

              {requestType === "feature_request" && (
                <div className="request-fields">
                  <FormInput labelText={label(localization, "ui.request_feature_title", "Feature title")} value={featureTitle} onChange={setFeatureTitle} maxLength={128} />
                  <FormTextarea labelText={label(localization, "ui.request_current_workflow", "Current workflow")} value={currentWorkflow} onChange={setCurrentWorkflow} />
                  <FormTextarea labelText={label(localization, "ui.request_requested_flow", "Requested flow")} value={requestedFlow} onChange={setRequestedFlow} />
                  <FormTextarea labelText={label(localization, "ui.request_expected_outcome", "Expected outcome")} value={expectedOutcome} onChange={setExpectedOutcome} />
                </div>
              )}

              {requestType === "ppt_report" && (
                <div className="request-fields">
                  <SearchCombobox
                    labelText={label(localization, "ui.request_country", "Country")}
                    placeholder={label(localization, "ui.request_country_placeholder_existing", "Search available countries")}
                    options={sortedCountries}
                    value={pptCountry}
                    mode="catalog"
                    allowCustom={false}
                    customAction={() => ""}
                    customStatus=""
                    emptyText={label(localization, "ui.request_no_matching_locations", "No matching locations")}
                    loadingText={label(localization, "ui.loading", "Loading")}
                    onChange={(value) => setPptCountry(value)}
                  />
                  <SegmentedControl
                    legend={label(localization, "ui.request_report_language", "Report language")}
                    name="report-locale"
                    value={reportLocale}
                    options={[{ value: "en", label: "EN" }, { value: "zh", label: "中文" }]}
                    onChange={(value) => setReportLocale(value as "en" | "zh")}
                  />
                </div>
              )}

              <div className="request-contact-section">
                <FormInput labelText={label(localization, "ui.request_contact_email", "Contact email")} value={email} onChange={setEmail} type="email" maxLength={320} />
                <p className="request-email-note">{label(localization, "ui.request_email_note", "We will use this email only for completion delivery and report attachments.")}</p>
              </div>
              {error && <div className="request-form-error" role="alert">{error}</div>}
            </div>

            <div className="service-drawer-actions">
              <button type="button" className="secondary" onClick={onClose}>{label(localization, "ui.close", "Close")}</button>
              <button type="submit" className="primary" disabled={submitting}>
                {submitting ? <LoaderCircle className="spin" size={16} aria-hidden="true" /> : <Send size={16} aria-hidden="true" />}
                <span>{submitting ? label(localization, "ui.request_submitting", "Submitting") : label(localization, "ui.request_submit", "Submit request")}</span>
              </button>
            </div>
          </form>
        )}
      </aside>
    </div>
  );
}

export function UpdatesDrawer({
  open,
  updates,
  loading,
  error,
  localization,
  onClose,
}: {
  open: boolean;
  updates: ProductUpdate[];
  loading: boolean;
  error: string;
  localization: LocalizationPayload;
  onClose: () => void;
}) {
  if (!open) {
    return null;
  }
  return (
    <div className="service-drawer-backdrop" data-guest-click-exempt="true" onMouseDown={onClose}>
      <aside className="service-drawer updates-drawer" role="dialog" aria-modal="true" aria-label={label(localization, "ui.updates_title", "Product updates")} onMouseDown={(event) => event.stopPropagation()}>
        <header className="service-drawer-head">
          <div>
            <Bell size={20} aria-hidden="true" />
            <div>
              <h2>{label(localization, "ui.updates_title", "Product updates")}</h2>
              <p>{label(localization, "ui.updates_intro", "Recently published scan and feature improvements.")}</p>
            </div>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label={label(localization, "ui.close", "Close")}><X size={18} /></button>
        </header>
        <div className="updates-list" data-testid="updates-list">
          {loading && <p>{label(localization, "ui.loading", "Loading")}</p>}
          {error && <p className="request-form-error">{error}</p>}
          {!loading && !error && updates.length === 0 && <p>{label(localization, "ui.updates_empty", "No published updates yet.")}</p>}
          {updates.map((update) => {
            const sceneLabels = update.scene_labels?.length
              ? update.scene_labels
              : update.scene_label ? [update.scene_label] : [];
            return (
              <article key={update.id} className="update-row">
                <div className="update-row-meta">
                  <span>{label(localization, update.category === "scan" ? "ui.update_scan" : "ui.update_feature", update.category)}</span>
                  <time dateTime={update.published_at}>{formatUpdateDate(update.published_at, localization.locale)}</time>
                </div>
                <h3>{update.title}</h3>
                <p>{update.summary}</p>
                {update.category === "scan" && (
                  <div className="update-scope">
                    {update.country && <span>{update.country}</span>}
                    {update.city && <span>{update.city}</span>}
                    {sceneLabels.map((scene) => <span key={scene}>{scene}</span>)}
                    {typeof update.actual_new_count === "number" && <strong>+{update.actual_new_count}</strong>}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </aside>
    </div>
  );
}

function RequestTypeButton({ active, icon, label: text, onClick }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void }) {
  return <button type="button" role="tab" aria-selected={active} className={active ? "active" : ""} onClick={onClick}>{icon}<span>{text}</span></button>;
}

function SearchCombobox({
  labelText,
  placeholder,
  options,
  value,
  mode,
  loading = false,
  allowCustom,
  customAction,
  customStatus,
  emptyText,
  loadingText,
  onChange,
}: {
  labelText: string;
  placeholder: string;
  options: string[];
  value: string;
  mode: LocationInputMode;
  loading?: boolean;
  allowCustom: boolean;
  customAction: (value: string) => string;
  customStatus: string;
  emptyText: string;
  loadingText: string;
  onChange: (value: string, mode: LocationInputMode) => void;
}) {
  const inputId = useId();
  const listId = `${inputId}-listbox`;
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (!open) {
      setQuery(value);
    }
  }, [open, value]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery(value);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [open, value]);

  const normalizedQuery = normalizeLocation(query);
  const exactOption = options.find((option) => normalizeLocation(option) === normalizedQuery);
  const filteredOptions = options
    .filter((option) => !normalizedQuery || normalizeLocation(option).includes(normalizedQuery))
    .slice(0, 80);
  const customValue = query.trim();
  const canCreateCustom = allowCustom && Boolean(customValue) && !exactOption;
  const itemCount = filteredOptions.length + (canCreateCustom ? 1 : 0);

  const choose = (nextValue: string, nextMode: LocationInputMode) => {
    onChange(nextValue.trim(), nextMode);
    setQuery(nextValue.trim());
    setOpen(false);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => itemCount > 0 ? (current + direction + itemCount) % itemCount : 0);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      setQuery(value);
      return;
    }
    if (event.key !== "Enter" || !open) {
      return;
    }
    event.preventDefault();
    if (activeIndex < filteredOptions.length) {
      choose(filteredOptions[activeIndex], "catalog");
    } else if (canCreateCustom) {
      choose(customValue, "custom");
    } else if (exactOption) {
      choose(exactOption, "catalog");
    }
  };

  return (
    <div className="combobox-field" ref={rootRef}>
      <label htmlFor={inputId}>{labelText}</label>
      <div className={`combobox-control ${open ? "open" : ""} ${mode === "custom" ? "custom" : ""}`}>
        <Search size={16} aria-hidden="true" />
        <input
          id={inputId}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={open && itemCount > 0 ? `${listId}-${activeIndex}` : undefined}
          value={query}
          placeholder={placeholder}
          autoComplete="off"
          onFocus={() => {
            setQuery(value);
            setOpen(true);
            setActiveIndex(0);
          }}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setActiveIndex(0);
          }}
          onKeyDown={handleKeyDown}
        />
        {query && (
          <button
            type="button"
            className="combobox-clear"
            aria-label={`Clear ${labelText}`}
            onClick={() => {
              setQuery("");
              onChange("", "catalog");
              setOpen(true);
            }}
          >
            <X size={15} />
          </button>
        )}
        <button type="button" className="combobox-toggle" aria-label={labelText} onClick={() => setOpen((current) => !current)}>
          <ChevronDown size={16} />
        </button>
      </div>
      {mode === "custom" && value && <span className="custom-location-note">{customStatus}</span>}
      {open && (
        <div className="combobox-popover" id={listId} role="listbox">
          {loading && <div className="combobox-state"><LoaderCircle className="spin" size={15} />{loadingText}</div>}
          {!loading && filteredOptions.map((option, index) => (
            <button
              type="button"
              role="option"
              id={`${listId}-${index}`}
              aria-selected={value === option && mode === "catalog"}
              className={activeIndex === index ? "active" : ""}
              key={option}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(option, "catalog")}
            >
              <span>{option}</span>
              {value === option && mode === "catalog" && <Check size={15} aria-hidden="true" />}
            </button>
          ))}
          {!loading && canCreateCustom && (
            <button
              type="button"
              role="option"
              id={`${listId}-${filteredOptions.length}`}
              aria-selected={false}
              className={`custom-option ${activeIndex === filteredOptions.length ? "active" : ""}`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(filteredOptions.length)}
              onClick={() => choose(customValue, "custom")}
            >
              <Plus size={15} aria-hidden="true" />
              <span>{customAction(customValue)}</span>
            </button>
          )}
          {!loading && filteredOptions.length === 0 && !canCreateCustom && (
            <div className="combobox-state">{emptyText}</div>
          )}
        </div>
      )}
    </div>
  );
}

function SegmentedControl({ legend, name, value, options, onChange }: { legend: string; name: string; value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) {
  return (
    <fieldset className="request-segmented-control">
      <legend>{legend}</legend>
      <div>
        {options.map((option) => (
          <label key={option.value} className={value === option.value ? "active" : ""}>
            <input type="radio" name={name} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function TargetStepper({ labelText, note, value, decrementLabel, incrementLabel, onChange }: { labelText: string; note: string; value: number; decrementLabel: string; incrementLabel: string; onChange: (value: number) => void }) {
  const clamp = (next: number) => onChange(Math.max(1, Math.min(10_000, next || 1)));
  return (
    <label className="target-stepper-field">
      <span>{labelText}</span>
      <div className="target-stepper">
        <button type="button" aria-label={decrementLabel} disabled={value <= 1} onClick={() => clamp(value - 1)}><Minus size={16} /></button>
        <input aria-label={labelText} type="number" min={1} max={10_000} value={value} onChange={(event) => clamp(Number(event.target.value))} required />
        <button type="button" aria-label={incrementLabel} disabled={value >= 10_000} onClick={() => clamp(value + 1)}><Plus size={16} /></button>
      </div>
      <small>{note}</small>
    </label>
  );
}

function FormInput({ labelText, value, onChange, type = "text", maxLength }: { labelText: string; value: string; onChange: (value: string) => void; type?: string; maxLength: number }) {
  return <label className="request-text-field"><span>{labelText}</span><input type={type} value={value} onChange={(event) => onChange(event.target.value)} maxLength={maxLength} required /></label>;
}

function FormTextarea({ labelText, value, onChange }: { labelText: string; value: string; onChange: (value: string) => void }) {
  return <label className="request-text-field"><span>{labelText}</span><textarea value={value} onChange={(event) => onChange(event.target.value)} maxLength={4000} rows={5} required /></label>;
}

function label(localization: LocalizationPayload, key: string, fallback: string): string {
  const parts = key.split(".");
  const lookup = (root: Record<string, any> | undefined) => parts.reduce<any>((current, part) => current?.[part], root);
  return String(lookup(localization.labels) ?? lookup(localization.fallback_labels) ?? fallback);
}

function formatLabel(localization: LocalizationPayload, key: string, fallback: string, value: string): string {
  return label(localization, key, fallback).replace("{value}", value);
}

function formatCountLabel(template: string, count: number): string {
  return template.replace("{count}", String(count));
}

function normalizeLocation(value: string): string {
  return value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").trim().toLocaleLowerCase();
}

function readApiDetail(payload: any): string {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item: any) => item?.msg).filter(Boolean).join("; ");
  }
  return "";
}

function formatUpdateDate(value: string, locale: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", { dateStyle: "medium" }).format(date);
}
