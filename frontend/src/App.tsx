import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { fetchRecommendations } from "./api";
import type {
  BookContentType,
  BookEdition,
  BookLanguageCode,
  BookRecommendation,
  BookLanguageConstraint,
  GenrePreference,
  LengthCategory,
  ReaderPreferenceOverrides,
  RecommendationResponse,
} from "./types";

const examplePrompt =
  "Me gustó «El nombre de la rosa» por la mezcla de misterio e historia. Busco una novela policial con una investigación compleja, personajes interesantes y una buena ambientación histórica. Prefiero leer en español, que tenga una extensión media y que no incluya violencia demasiado explícita.";

const loadingMessages = [
  "Entendiendo tu solicitud y tus preferencias…",
  "Buscando libros que coincidan con lo que pediste…",
  "Comparando candidatos y afinando la selección…",
  "Revisando ediciones y disponibilidad en librerías…",
  "Las consultas externas siguen en curso; la espera puede extenderse un poco más.",
];

const genreOptions: { value: GenrePreference; label: string }[] = [
  { value: "detective fiction", label: "Policial y detectives" },
  { value: "mystery", label: "Misterio" },
  { value: "thriller", label: "Suspenso y thriller" },
  { value: "science fiction", label: "Ciencia ficción" },
  { value: "fantasy", label: "Fantasía" },
  { value: "horror", label: "Terror" },
  { value: "romance", label: "Romance" },
  { value: "historical fiction", label: "Histórica" },
  { value: "adventure", label: "Aventura" },
  { value: "literary fiction", label: "Literaria" },
  { value: "humor", label: "Humor" },
];

const languageOptions: { value: BookLanguageCode; label: string }[] = [
  { value: "spa", label: "Español" },
  { value: "eng", label: "Inglés" },
  { value: "por", label: "Portugués" },
  { value: "fra", label: "Francés" },
  { value: "ita", label: "Italiano" },
  { value: "deu", label: "Alemán" },
  { value: "jpn", label: "Japonés" },
];

const lengthOptions: {
  value: LengthCategory | "from_text";
  label: string;
  description?: string;
}[] = [
  { value: "from_text", label: "Según lo que escriba" },
  { value: "any", label: "Sin preferencia de extensión" },
  {
    value: "short",
    label: "Corto",
    description: "Prioriza libros de hasta 200 páginas.",
  },
  {
    value: "medium",
    label: "Mediano",
    description: "Prioriza libros de aproximadamente 150 a 350 páginas.",
  },
  {
    value: "long",
    label: "Largo",
    description: "Prioriza libros de más de 400 páginas.",
  },
];

const contentTypeOptions: {
  value: BookContentType | "from_text";
  label: string;
}[] = [
  { value: "from_text", label: "Según lo que escriba" },
  { value: "novel", label: "Novela" },
  { value: "short story collection", label: "Colección de cuentos" },
  { value: "essay", label: "Ensayo / no ficción" },
  { value: "poetry", label: "Poesía" },
  { value: "graphic novel", label: "Novela gráfica / cómic" },
];

type CustomizationId =
  | "preferredGenres"
  | "excludedGenres"
  | "languages"
  | "contentType"
  | "length";

const customizationOptions: {
  id: CustomizationId;
  label: string;
}[] = [
  { id: "preferredGenres", label: "Géneros que te interesan" },
  { id: "excludedGenres", label: "Géneros que querés evitar" },
  { id: "languages", label: "Idioma de lectura" },
  { id: "contentType", label: "Tipo de obra" },
  { id: "length", label: "Extensión aproximada" },
];

function OptionChecklist<T extends string>({
  title,
  options,
  selected,
  onToggle,
}: {
  title: string;
  options: { value: T; label: string }[];
  selected: T[];
  onToggle: (value: T) => void;
}) {
  return (
    <details className="filter-dropdown">
      <summary>
        <span>{title}</span>
        <span className="filter-dropdown-count">
          {selected.length > 0
            ? `${selected.length} seleccionado(s)`
            : "Sin selección"}
        </span>
      </summary>
      <div className="filter-dropdown-options">
        {options.map((option) => (
          <label className="filter-option" key={option.value}>
            <input
              type="checkbox"
              checked={selected.includes(option.value)}
              onChange={() => onToggle(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </details>
  );
}

function toggleOption<T extends string>(
  selected: T[],
  value: T,
  update: (values: T[]) => void,
) {
  update(
    selected.includes(value)
      ? selected.filter((item) => item !== value)
      : [...selected, value],
  );
}

function selectedLabels<T extends string>(
  options: { value: T; label: string }[],
  selected: T[],
) {
  return options
    .filter((option) => selected.includes(option.value))
    .map((option) => option.label)
    .join(" · ");
}

function VisibilityIcon({ hidden }: { hidden: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {hidden ? (
        <>
          <path d="M3 3l18 18" />
          <path d="M10.6 10.6a2 2 0 002.8 2.8" />
          <path d="M9.9 5.2A10.7 10.7 0 0112 5c5.5 0 9 7 9 7a15.7 15.7 0 01-3 3.8" />
          <path d="M6.2 6.2C3.9 7.8 3 12 3 12s3.5 7 9 7a9.7 9.7 0 004.1-.9" />
        </>
      ) : (
        <>
          <path d="M2.5 12s3.5-7 9.5-7 9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7Z" />
          <circle cx="12" cy="12" r="2.5" />
        </>
      )}
    </svg>
  );
}

function CustomizationIcon({ id }: { id: CustomizationId }) {
  const iconContent = {
    preferredGenres: (
      <path d="M20.8 8.8c0 4.4-8.8 10-8.8 10s-8.8-5.6-8.8-10a4.7 4.7 0 018.8-2.2 4.7 4.7 0 018.8 2.2Z" />
    ),
    excludedGenres: (
      <>
        <circle cx="12" cy="12" r="8.5" />
        <path d="m6 6 12 12" />
      </>
    ),
    languages: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M3.5 12h17M12 3c2.2 2.4 3.2 5.4 3.2 9s-1 6.6-3.2 9c-2.2-2.4-3.2-5.4-3.2-9S9.8 5.4 12 3Z" />
      </>
    ),
    contentType: (
      <>
        <path d="M3 5.5c3-.9 6.1-.4 9 1.5v13c-2.9-1.9-6-2.4-9-1.5v-13Z" />
        <path d="M21 5.5c-3-.9-6.1-.4-9 1.5v13c2.9-1.9 6-2.4 9-1.5v-13Z" />
      </>
    ),
    length: (
      <>
        <path d="M6 3h12M6 21h12M7 3c0 4 5 5 5 9s-5 5-5 9m10-18c0 4-5 5-5 9s5 5 5 9" />
        <path d="M9 16h6" />
      </>
    ),
  }[id];

  return (
    <svg
      className={`customization-choice-symbol customization-icon-${id}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {iconContent}
    </svg>
  );
}

function splitByMarket(offers: BookEdition[]) {
  return {
    argentina: offers.filter((offer) => offer.market === "Argentina"),
    international: offers.filter(
      (offer) => offer.market === "Internacional",
    ),
  };
}

function OfferList({ offers }: { offers: BookEdition[] }) {
  if (offers.length === 0) {
    return <p className="empty-note">No se confirmó una oferta disponible.</p>;
  }

  return (
    <ul className="offer-list">
      {offers.map((offer) => (
        <li key={`${offer.retailer}-${offer.url}`}>
          <a href={offer.url} target="_blank" rel="noreferrer">
            <span className="offer-retailer">{offer.retailer}</span>
            <span className="offer-title">{offer.title}</span>
            <span className="offer-link" aria-hidden="true">
              ↗
            </span>
          </a>
          {offer.availability_evidence && (
            <p className="offer-evidence">{offer.availability_evidence}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function RecommendationCard({
  book,
  index,
}: {
  book: BookRecommendation;
  index: number;
}) {
  const offers = splitByMarket(book.purchase_options);
  const retailerCount = new Set(
    book.purchase_options.map(
      (offer) => `${offer.market}:${offer.retailer.trim().toLowerCase()}`,
    ),
  ).size;

  return (
    <article className="book-card">
      <div className="book-card-topline">
        <span className="book-number">0{index + 1}</span>
        {book.is_best_match && <span className="best-match">Mejor opción</span>}
        <span className="match-score">{book.match_score}% de afinidad</span>
      </div>

      <h2>{book.title}</h2>
      <p className="book-author">{book.author}</p>
      <p className="book-introduction">{book.introduction}</p>

      <div className="book-details">
        <div>
          <span className="detail-label">Por qué puede gustarte</span>
          <p>{book.why_it_matches}</p>
        </div>
        {book.potential_drawbacks && (
          <div>
            <span className="detail-label">A tener en cuenta</span>
            <p>{book.potential_drawbacks}</p>
          </div>
        )}
      </div>

      <div className="book-meta">
        <span>{book.approximate_length}</span>
        <span>{book.language}</span>
      </div>

      <section className="offers-section" aria-label="Opciones de compra">
        <details className="retailer-details">
          <summary>
            <span className="retailer-availability">
              {retailerCount > 0
                ? `Disponible en: ${retailerCount} ${
                    retailerCount === 1 ? "lugar" : "lugares"
                  }`
                : "Sin opciones de compra"}
            </span>
            <span className="retailer-details-label">
              <span className="retailer-open-label">Ver detalles</span>
              <span className="retailer-close-label">Ocultar detalles</span>
            </span>
          </summary>
          {retailerCount > 0 ? (
            <div className="retailer-details-content">
              <div className="offer-market">
                <h3>Argentina</h3>
                <OfferList offers={offers.argentina} />
              </div>
              {offers.international.length > 0 && (
                <div className="offer-market">
                  <h3>Internacional</h3>
                  <OfferList offers={offers.international} />
                </div>
              )}
            </div>
          ) : (
            <p className="empty-note">No se encontraron librerías para este libro.</p>
          )}
        </details>
      </section>
    </article>
  );
}

function Results({
  data,
}: {
  data: RecommendationResponse;
}) {
  if (data.recommendations.length === 0) {
    return (
      <div className="empty-results">
        <span className="empty-mark" aria-hidden="true">
          ∅
        </span>
        <h2>No encontramos recomendaciones esta vez</h2>
        <p>Probá con más detalles sobre el género, el tono o el idioma.</p>
      </div>
    );
  }

  return (
    <section className="results-section" aria-label="Recomendaciones">
      <div className="results-heading">
        <div>
          <span className="eyebrow">TU PRÓXIMA LECTURA</span>
          <h2>
            Historias para <em>descubrir</em>
          </h2>
        </div>
        <span className="results-count">
          {data.recommendations.length} sugerencia(s)
        </span>
      </div>
      <div className="book-list">
        {data.recommendations.map((book, index) => (
          <RecommendationCard
            key={`${book.title}-${book.author}`}
            book={book}
            index={index}
          />
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const [userInput, setUserInput] = useState("");
  const [data, setData] = useState<RecommendationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadingMessageIndex, setLoadingMessageIndex] = useState(0);
  const [preferredGenres, setPreferredGenres] = useState<GenrePreference[]>([]);
  const [excludedGenres, setExcludedGenres] = useState<GenrePreference[]>([]);
  const [preferredLanguages, setPreferredLanguages] = useState<
    BookLanguageCode[]
  >([]);
  const [languageConstraint, setLanguageConstraint] =
    useState<BookLanguageConstraint>("preferred");
  const [contentType, setContentType] =
    useState<BookContentType | "from_text">("from_text");
  const [lengthCategory, setLengthCategory] =
    useState<LengthCategory | "from_text">("from_text");
  const [activeCustomizations, setActiveCustomizations] = useState<
    CustomizationId[]
  >([]);
  const [hiddenCustomizations, setHiddenCustomizations] = useState<
    CustomizationId[]
  >([]);

  useEffect(() => {
    if (!isLoading) {
      return;
    }

    // Los mensajes avanzan por tiempo transcurrido; no representan progreso
    // medido en el backend ni un porcentaje de tareas completadas.
    const timer = window.setInterval(() => {
      setLoadingMessageIndex((current) =>
        Math.min(current + 1, loadingMessages.length - 1),
      );
    }, 8000);

    return () => window.clearInterval(timer);
  }, [isLoading]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = userInput.trim();
    if (prompt.length < 3 || isLoading) {
      return;
    }

    setIsLoading(true);
    setLoadingMessageIndex(0);
    setError(null);
    setData(null);
    const preferences: ReaderPreferenceOverrides = {};
    if (
      activeCustomizations.includes("preferredGenres") &&
      preferredGenres.length > 0
    ) {
      preferences.preferred_genres = preferredGenres;
    }
    if (
      activeCustomizations.includes("excludedGenres") &&
      excludedGenres.length > 0
    ) {
      preferences.excluded_genres = excludedGenres;
    }
    if (
      activeCustomizations.includes("languages") &&
      preferredLanguages.length > 0
    ) {
      preferences.preferred_book_languages = preferredLanguages;
      preferences.book_language_constraint = languageConstraint;
    }
    if (
      activeCustomizations.includes("contentType") &&
      contentType !== "from_text"
    ) {
      preferences.preferred_content_type = contentType;
    }
    if (
      activeCustomizations.includes("length") &&
      lengthCategory !== "from_text"
    ) {
      preferences.preferred_length_category = lengthCategory;
    }
    try {
      setData(await fetchRecommendations(prompt, preferences));
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Ocurrió un error inesperado.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  function startOver() {
    setData(null);
    setError(null);
    setUserInput("");
    setPreferredGenres([]);
    setExcludedGenres([]);
    setPreferredLanguages([]);
    setLanguageConstraint("preferred");
    setContentType("from_text");
    setLengthCategory("from_text");
    setActiveCustomizations([]);
    setHiddenCustomizations([]);
  }

  function addCustomization(id: CustomizationId) {
    setActiveCustomizations((current) =>
      current.includes(id) ? current : [...current, id],
    );
  }

  function removeCustomization(id: CustomizationId) {
    setActiveCustomizations((current) =>
      current.filter((customization) => customization !== id),
    );
    setHiddenCustomizations((current) =>
      current.filter((customization) => customization !== id),
    );

    switch (id) {
      case "preferredGenres":
        setPreferredGenres([]);
        break;
      case "excludedGenres":
        setExcludedGenres([]);
        break;
      case "languages":
        setPreferredLanguages([]);
        setLanguageConstraint("preferred");
        break;
      case "contentType":
        setContentType("from_text");
        break;
      case "length":
        setLengthCategory("from_text");
        break;
    }
  }

  function toggleCustomizationVisibility(id: CustomizationId) {
    setHiddenCustomizations((current) =>
      current.includes(id)
        ? current.filter((customization) => customization !== id)
        : [...current, id],
    );
  }

  function customizationSummary(id: CustomizationId) {
    switch (id) {
      case "preferredGenres":
        return selectedLabels(genreOptions, preferredGenres) || "Sin selección";
      case "excludedGenres":
        return selectedLabels(genreOptions, excludedGenres) || "Sin selección";
      case "languages":
        return preferredLanguages.length > 0
          ? `${selectedLabels(languageOptions, preferredLanguages)} · ${
              languageConstraint === "exclusive"
                ? "Solo esos idiomas"
                : "Preferencia flexible"
            }`
          : "Elegí uno o más idiomas";
      case "contentType":
        return (
          contentTypeOptions.find((option) => option.value === contentType)
            ?.label ?? "Según lo que escribas"
        );
      case "length":
        return (
          lengthOptions.find((option) => option.value === lengthCategory)
            ?.label ?? "Según lo que escribas"
        );
    }
  }

  function renderCustomizationOptions(id: CustomizationId) {
    switch (id) {
      case "preferredGenres":
        return (
          <OptionChecklist
            title="Elegí uno o más géneros"
            options={genreOptions}
            selected={preferredGenres}
            onToggle={(value) =>
              toggleOption(preferredGenres, value, setPreferredGenres)
            }
          />
        );
      case "excludedGenres":
        return (
          <OptionChecklist
            title="Elegí los géneros que querés evitar"
            options={genreOptions}
            selected={excludedGenres}
            onToggle={(value) =>
              toggleOption(excludedGenres, value, setExcludedGenres)
            }
          />
        );
      case "languages":
        return (
          <>
            <OptionChecklist
              title="Elegí uno o más idiomas"
              options={languageOptions}
              selected={preferredLanguages}
              onToggle={(value) =>
                toggleOption(
                  preferredLanguages,
                  value,
                  setPreferredLanguages,
                )
              }
            />
            {preferredLanguages.length > 0 && (
              <div className="language-constraint">
                <span>¿Qué tan estricta?</span>
                <label>
                  <input
                    type="radio"
                    name="language-constraint"
                    value="preferred"
                    checked={languageConstraint === "preferred"}
                    onChange={() => setLanguageConstraint("preferred")}
                  />
                  Los prefiero, pero acepto otros
                </label>
                <label>
                  <input
                    type="radio"
                    name="language-constraint"
                    value="exclusive"
                    checked={languageConstraint === "exclusive"}
                    onChange={() => setLanguageConstraint("exclusive")}
                  />
                  Solo esos idiomas
                </label>
              </div>
            )}
          </>
        );
      case "contentType":
        return (
          <label className="select-filter">
            <span>Elegí un tipo de obra</span>
            <select
              value={contentType}
              onChange={(event) => {
                const selected = contentTypeOptions.find(
                  (option) => option.value === event.target.value,
                );
                if (selected) setContentType(selected.value);
              }}
            >
              {contentTypeOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        );
      case "length":
        return (
          <label className="select-filter">
            <span>Elegí la extensión aproximada</span>
            <select
              value={lengthCategory}
              onChange={(event) => {
                const selected = lengthOptions.find(
                  (option) => option.value === event.target.value,
                );
                if (selected) setLengthCategory(selected.value);
              }}
            >
              {lengthOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.description
                    ? `${option.label} — ${option.description}`
                    : option.label}
                </option>
              ))}
            </select>
            <small>
              Es una preferencia de ranking; los libros sin páginas informadas
              no se descartan.
            </small>
          </label>
        );
    }
  }

  return (
    <main className="app-shell">
      <div className="page-decorations" aria-hidden="true">
        <span className="decoration-orb decoration-orb-coral" />
        <span className="decoration-orb decoration-orb-purple" />
        <div className="decoration-books">
          <span />
          <span />
          <span />
        </div>
      </div>
      <header className="site-header">
        <a className="wordmark" href="/" aria-label="Book&More, inicio">
          <span className="wordmark-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 7c-2.7-2-5.9-2.5-9-1.6v12c3.1-.9 6.3-.4 9 1.6V7Z"
                fill="#ff796b"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinejoin="round"
              />
              <path
                d="M12 7c2.7-2 5.9-2.5 9-1.6v12c-3.1-.9-6.3-.4-9 1.6V7Z"
                fill="#fff9f3"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinejoin="round"
              />
              <path d="M12 7v12" stroke="currentColor" strokeWidth="1.4" />
            </svg>
          </span>
          <span>Book&amp;More</span>
        </a>
        <span className="header-note">LECTURAS A TU MEDIDA</span>
      </header>

      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">UN LIBRO PUEDE CAMBIARTE EL DÍA</span>
          <h1>
            ¿Qué te gustaría
            <br />
            <em>leer hoy?</em>
          </h1>
          <p>
            Contanos qué historia estás buscando. Encontramos libros que
            conectan con vos y dónde conseguirlos.
          </p>
        </div>

        <form className="prompt-card" onSubmit={handleSubmit}>
          <label htmlFor="reading-prompt">Tu próxima lectura empieza acá</label>
          <textarea
            id="reading-prompt"
            name="user_input"
            maxLength={4000}
            minLength={3}
            placeholder="Me gustó «El nombre de la rosa» por la mezcla de misterio e historia. Busco una novela policial con una investigación compleja, personajes interesantes y una buena ambientación histórica. Prefiero leer en español, que tenga una extensión media y que no incluya violencia demasiado explícita."
            required
            value={userInput}
            onChange={(event) => setUserInput(event.target.value)}
            disabled={isLoading}
          />
          <div className="prompt-hint">
            <button
              type="button"
              className="example-button"
              onClick={() => setUserInput(examplePrompt)}
              disabled={isLoading}
            >
              Probar un ejemplo
            </button>
            <span>{userInput.length}/4000</span>
          </div>
          <section className="personalization-section">
            <div className="personalization-heading">
              <div>
                <h2>Personalizá tu búsqueda</h2>
                <p>
                  Los filtros son opcionales: abajo podés elegir géneros,
                  idioma, tipo de obra y extensión. Usá el ojo para ocultar o
                  volver a mostrar opciones sin borrar tus elecciones.
                </p>
              </div>
            </div>
            <div className="customization-picker" aria-label="Personalizaciones">
              {customizationOptions.map((option) => {
                const isAdded = activeCustomizations.includes(option.id);
                return (
                  <button
                    className={`customization-choice${isAdded ? " is-added" : ""}`}
                    type="button"
                    key={option.id}
                    disabled={isAdded || isLoading}
                    onClick={() => addCustomization(option.id)}
                  >
                    <span className="customization-choice-label">
                      <CustomizationIcon id={option.id} />
                      <span>{option.label}</span>
                    </span>
                    <span className="customization-choice-icon" aria-hidden="true">
                      {isAdded ? "✓" : "+"}
                    </span>
                  </button>
                );
              })}
            </div>
            {activeCustomizations.length > 0 && (
              <div className="customization-blocks">
                {activeCustomizations.map((id) => {
                  const option = customizationOptions.find(
                    (item) => item.id === id,
                  );
                  if (!option) return null;
                  const isHidden = hiddenCustomizations.includes(id);

                  return (
                    <section
                      className={`customization-block${isHidden ? " is-hidden" : ""}`}
                      key={id}
                      aria-label={option.label}
                    >
                      <div className="customization-block-heading">
                        <div>
                          <h3>{option.label}</h3>
                          <p>{customizationSummary(id)}</p>
                        </div>
                        <div className="customization-actions">
                          <button
                            className="visibility-toggle"
                            type="button"
                            aria-label={`${isHidden ? "Mostrar" : "Ocultar"} opciones de ${option.label}`}
                            aria-expanded={!isHidden}
                            disabled={isLoading}
                            onClick={() => toggleCustomizationVisibility(id)}
                          >
                            <VisibilityIcon hidden={isHidden} />
                          </button>
                          <button
                            className="remove-customization"
                            type="button"
                            aria-label={`Quitar ${option.label}`}
                            disabled={isLoading}
                            onClick={() => removeCustomization(id)}
                          >
                            −
                          </button>
                        </div>
                      </div>
                      {!isHidden && (
                        <div className="customization-block-options">
                          {renderCustomizationOptions(id)}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>
            )}
          </section>
          <button
            className="submit-button"
            type="submit"
            disabled={isLoading || userInput.trim().length < 3}
          >
            {isLoading ? (
              <>
                <span className="spinner" aria-hidden="true" />
                Preparando tus recomendaciones…
              </>
            ) : (
              <>
                Encontrar libros <span aria-hidden="true">→</span>
              </>
            )}
          </button>
          {isLoading && (
            <div
              className="loading-note"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              <strong>{loadingMessages[loadingMessageIndex]}</strong>
              <p>
                Puede tardar un par de minutos o más, según la respuesta de los
                servicios de búsqueda. No cierres ni recargues esta página.
              </p>
            </div>
          )}
        </form>
      </section>

      {error && (
        <div className="error-banner" role="alert">
          <span className="error-icon" aria-hidden="true">
            !
          </span>
          <div>
            <strong>No pudimos completar la búsqueda</strong>
            <p>{error}</p>
          </div>
        </div>
      )}

      {data && <Results data={data} />}

      {data && (
        <button className="reset-button" type="button" onClick={startOver}>
          ← Hacer otra búsqueda
        </button>
      )}

      <footer className="site-footer">
        <span>Una buena historia siempre encuentra su momento.</span>
        <span>ENTRE LÍNEAS · 2026</span>
      </footer>
    </main>
  );
}
