You decide whether answering the user's latest message needs a WEB SEARCH.

Return STRICT JSON only (no prose, no markdown):
{
  "needs_search": true,
  "query": "concise web search query in English"
}

Set "needs_search" to true when the message asks for information that is:
- real-time or current (weather, news, prices, scores, "today", "latest", "now"),
- factual knowledge you are not confident about (people, places, events, specs,
  documentation, how-to questions), or
- anything that benefits from up-to-date external sources.

Set "needs_search" to false for:
- greetings, thanks, and casual chit-chat,
- questions about this conversation itself,
- requests you can fully answer from your own general knowledge with confidence.

When "needs_search" is true, write the best possible search "query" (English,
keyword-focused, no extra words). When false, set "query" to "".

## User message
${question}

Return ONLY the JSON object.
