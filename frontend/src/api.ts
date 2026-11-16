import type {
  ReaderPreferenceOverrides,
  RecommendationResponse,
} from "./types";

export async function fetchRecommendations(
  userInput: string,
  preferences: ReaderPreferenceOverrides,
): Promise<RecommendationResponse> {
  const response = await fetch("/api/recommendations", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      user_input: userInput,
      preferences,
    }),
  });

  if (!response.ok) {
    let message = `La solicitud falló (HTTP ${response.status}).`;
    try {
      const error = (await response.json()) as { detail?: string };
      if (error.detail) {
        message = error.detail;
      }
    } catch {
      // Keep the HTTP status message when the server does not return JSON.
    }
    throw new Error(message);
  }

  return (await response.json()) as RecommendationResponse;
}
