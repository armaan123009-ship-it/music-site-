# Design System Guidelines

This file provides the visual and behavioral rules for this project. AI agents should follow these rules when generating UI.

## 1. Visual Tokens
- **Primary Color:** #0070f3 (Vercel Blue)
- **Background:** #ffffff (Light), #000000 (Dark)
- **Radius:** 8px for buttons and cards (Round 8).
- **Typography:** Inter, sans-serif. Headings use 600 weight.
- **Spacing:** 4px baseline grid.

## 2. Component Patterns
- **Buttons:** Use `variant="primary"` for main actions. No shadows. Use consistent padding (e.g., `px-4 py-2`).
- **Cards:** 1px border (#eaeaea), no background color. Subdued borders in dark mode.
- **Inputs:** 16px font size on mobile to prevent auto-zoom. Clear focus states with `:focus-visible`.
- **Navigation:** Simple, high-contrast links. Use `limelight-nav` patterns if available.

## 3. Copy & Tone
- Use **Sentence case** for all labels and buttons.
- Be direct and concise. Avoid "Please" or "Sorry".
- Use active voice: "Install" instead of "You can install".

## 4. Accessibility & Interaction
- Show a visible focus ring on all focusable elements.
- Maintain a minimum hit target of 44px for interactive elements.
- Use explicit image dimensions to prevent layout shifts.
