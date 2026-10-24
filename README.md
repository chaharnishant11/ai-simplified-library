# The Library · AI Simplified

Every post from [AI Simplified](https://nishantchahar.substack.com) by Nishant Chahar, as a digital library. Each post is a book on a shelf: hover one and it slides out, turns to its cover and shows a line from the post. The page also has Notes, the reading list (Substack recommendations), About, sponsorships, and the subscription plans.

**Live:** https://chaharnishant11.github.io/ai-simplified-library/

## How it stays current

`.github/workflows/build.yml` runs every hour. It runs `build_library.py`, which reads Substack's public data (posts, prices, benefits, subscriber count, Notes, recommendations), then publishes to GitHub Pages. A new post appears on its own within the hour.

If a fetch fails, that section keeps the last snapshot in `data.json`.

## Editing

- **Shelves, spine titles, colours, quotes, tool cubes, About and sponsor copy:** edit `shelves.json`, then push.
- **A quote must be copied word for word from its post.** The build fails otherwise.
- **Preview locally:** run `python3 -I build_library.py`, then open `index.html` through any static server.

## Credits

- **Fonts:** Inter, Instrument Serif and JetBrains Mono, all under the SIL Open Font License (see `fonts/OFL-*.txt`).
- **Tool logos:** these belong to their owners and are shown only to name the tools the posts cover. The Gemini mark is from Simple Icons.
