export type GenrePreference =
  | "mystery"
  | "detective fiction"
  | "thriller"
  | "science fiction"
  | "fantasy"
  | "horror"
  | "romance"
  | "historical fiction"
  | "adventure"
  | "literary fiction"
  | "humor";

export type BookLanguageCode =
  | "spa"
  | "eng"
  | "por"
  | "fra"
  | "ita"
  | "deu"
  | "jpn";

export type BookContentType =
  | "novel"
  | "short story collection"
  | "essay"
  | "poetry"
  | "graphic novel";

export type LengthCategory = "short" | "medium" | "long" | "any";
export type BookLanguageConstraint = "preferred" | "exclusive";

export interface ReaderPreferenceOverrides {
  preferred_genres?: GenrePreference[];
  excluded_genres?: GenrePreference[];
  preferred_book_languages?: BookLanguageCode[];
  book_language_constraint?: "none" | BookLanguageConstraint;
  preferred_length_category?: LengthCategory;
  preferred_content_type?: BookContentType;
}

export interface BookEdition {
  retailer: string;
  market: "Argentina" | "Internacional";
  title: string;
  author: string | null;
  url: string;
  language: string | null;
  isbn: string | null;
  publisher: string | null;
  format: string | null;
  pages: number | null;
  availability: "available" | "unavailable" | "unverified";
  availability_evidence: string | null;
  shipping_to_argentina: string;
}

export interface BookRecommendation {
  title: string;
  author: string;
  open_library_id: string | null;
  match_score: number;
  introduction: string;
  why_it_matches: string;
  potential_drawbacks: string;
  approximate_length: string;
  language: string;
  is_best_match: boolean;
  purchase_options: BookEdition[];
}

export interface RecommendationResponse {
  recommendations: BookRecommendation[];
}
