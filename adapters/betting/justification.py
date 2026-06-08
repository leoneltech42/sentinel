"""LLM-based pick justification — a short, human-readable "why this pick"
blurb generated from a signal's features and stored in `features.justification`
(jsonb — no schema change).

Works with any OpenAI-compatible chat-completions endpoint (Groq, OpenAI,
Anthropic's OpenAI-compat shim, local vLLM/Ollama, etc.) so the provider can be
swapped via config without touching code. Defaults to Groq's free tier.

Never called in --mock mode; failures degrade gracefully (return None — the
pipeline continues without a justification, never crashes).
"""

from __future__ import annotations

import logging

import requests


class LLMJustifier:
    """Generates a short pick justification via an OpenAI-compatible chat API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.groq.com/openai/v1",
        model: str = "llama-3.3-70b-versatile",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, features: dict) -> str | None:
        """Return a 2-3 sentence justification for the pick, or None on failure."""
        pick = features.get("pick", "")
        home = features.get("home_team", "")
        away = features.get("away_team", "")
        opponent = away if pick == home else home
        home_era = features.get("home_pitcher_era")
        away_era = features.get("away_pitcher_era")
        home_pitcher = features.get("home_pitcher", "unknown")
        away_pitcher = features.get("away_pitcher", "unknown")

        pitcher_context = ""
        if home_era and away_era:
            pitcher_context = (
                f"Home pitcher ({home_pitcher}): ERA {home_era:.2f}. "
                f"Away pitcher ({away_pitcher}): ERA {away_era:.2f}. "
                f"League average ERA: 4.20."
            )

        user_msg = f"""
Pick: {pick} to win vs {opponent}
Our model probability: {features.get('model_probability', 0):.1%}
Market implied probability: {features.get('market_probability', 0):.1%}
Edge over market: {features.get('edge', 0):+.1%}
Best available odd: {features.get('best_odd', 0)}
{pitcher_context}

Write a sharp, conversational 2-3 sentence explanation of why
this pick has value. Be specific about the numbers. Sound like
an experienced analyst talking to a friend, not a robot listing
data. Under 60 words. No phrases like "Based on our model" or
"The algorithm says". English only.
"""

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a sharp sports betting analyst. "
                        "Give concise, data-driven pick justifications. "
                        "Be specific, confident, and conversational."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 120,
            "temperature": 0.7,
        }

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logging.warning(f"LLM justification failed: {e}")
            return None
