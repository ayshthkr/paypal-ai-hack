---
version: alpha
name: PayPal
slug: paypal
source: https://paypal.com
extractedAt: 2026-05-17
description: "Trust-first payments identity with assertive PayPal blues, black-and-white clarity, pill-shaped commerce CTAs, compact geometric typography, and modular pay, send, save, checkout, and pricing patterns."

colors:
  primary: "#003087"
  accent: "#0070E0"
  accentHover: "#005EA6"
  accentPressed: "#001C64"
  ink: "#001435"
  body: "#2C2E2F"
  muted: "#687173"
  canvas: "#FFFFFF"
  surface: "#FFFFFF"
  surfaceAlt: "#FAF8F5"
  border: "#DADDE1"
  borderStrong: "#B7BDC2"
  link: "#0070E0"
  success: "#0A7A3D"
  warning: "#FFB600"
  error: "#D9360B"
  darkBlue: "#003087"
  middleBlue: "#0070E0"
  rightBlue: "#009CDE"
  deepNavy: "#001C64"
  slateNavy: "#001435"
  venmoBlue: "#008CFF"
  payLaterGold: "#FFD140"
  checkoutYellow: "#FFC439"
  codeCanvas: "#F6F8FA"
  footerBg: "#F7F9FA"
  on-primary: "#FFFFFF"
  on-accent: "#FFFFFF"
  on-yellow: "#001435"
  on-dark: "#FFFFFF"

typography:
  display:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 76px
    fontWeight: 700
    lineHeight: 0.95
    letterSpacing: "-0.045em"
  hero:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 64px
    fontWeight: 700
    lineHeight: 1.0
    letterSpacing: "-0.04em"
  headline-lg:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 48px
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: "-0.035em"
  title-lg:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 36px
    fontWeight: 700
    lineHeight: 1.14
    letterSpacing: "-0.025em"
  title-md:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 28px
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.018em"
  title-sm:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 22px
    fontWeight: 700
    lineHeight: 1.27
    letterSpacing: "-0.01em"
  body:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 18px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
  body-sm:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
  label:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 14px
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "0.005em"
  button:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 16px
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "0em"
  caption:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "0em"
  legal:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "0em"
  pricing-display:
    fontFamily: "PayPal Pro, PayPal Open, Helvetica Neue, Arial, sans-serif"
    fontSize: 56px
    fontWeight: 700
    lineHeight: 0.95
    letterSpacing: "-0.045em"
  code:
    fontFamily: "SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "0em"

rounded:
  sm: 4px
  md: 8px
  lg: 16px
  xl: 28px
  pill: 9999px

spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  "2xl": 64px
  section: 96px

components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.button}"
    rounded: "{rounded.pill}"
    padding: 14px 28px
  button-primary-active:
    backgroundColor: "{colors.accentPressed}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.pill}"
  button-secondary:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.primary}"
    borderColor: "{colors.primary}"
    typography: "{typography.button}"
    rounded: "{rounded.pill}"
    padding: 13px 27px
  button-secondary-on-dark:
    backgroundColor: "transparent"
    textColor: "{colors.on-dark}"
    borderColor: "{colors.on-dark}"
    typography: "{typography.button}"
    rounded: "{rounded.pill}"
    padding: 13px 27px
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.primary}"
    typography: "{typography.button}"
    rounded: "{rounded.pill}"
    padding: 12px 16px
  button-checkout-yellow:
    backgroundColor: "{colors.checkoutYellow}"
    textColor: "{colors.on-yellow}"
    typography: "{typography.button}"
    rounded: "{rounded.pill}"
    padding: 14px 28px
  nav-shell:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    borderColor: "{colors.border}"
    typography: "{typography.label}"
    padding: 16px 48px
  hero-band:
    backgroundColor: "{colors.surfaceAlt}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xl}"
    padding: 72px 64px
  feature-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    borderColor: "{colors.border}"
    padding: 28px 28px
  pricing-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xl}"
    borderColor: "{colors.border}"
    padding: 32px 32px
  pricing-rate:
    textColor: "{colors.ink}"
    typography: "{typography.pricing-display}"
  tab-pill:
    backgroundColor: "{colors.surfaceAlt}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: 8px 16px
  tab-pill-active:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.pill}"
    padding: 8px 16px
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    borderColor: "{colors.borderStrong}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: 14px 16px
  input-focus:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    borderColor: "{colors.accent}"
    rounded: "{rounded.md}"
  checkout-module:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    shadow: "0 2px 12px rgba(0, 20, 53, 0.12)"
    padding: 24px 24px
  code-block:
    backgroundColor: "{colors.codeCanvas}"
    textColor: "{colors.ink}"
    typography: "{typography.code}"
    rounded: "{rounded.md}"
    padding: 16px 20px
  footer:
    backgroundColor: "{colors.footerBg}"
    textColor: "{colors.body}"
    typography: "{typography.caption}"
    padding: 48px 48px
---

## Overview

PayPal's web identity is a high-trust commerce system: bright enough to feel digital, restrained enough to feel financial. It is anchored by deep PayPal blues, black-and-white clarity, pill-shaped commerce CTAs, large compact headlines, and modular product stories around paying, sending, saving, checkout, and business pricing.

The brand has two active modes. Marketing pages use oversized geometric type, warm off-white bands, large rounded product panels, and short verb-led stories such as pay, send, and save. Utility pages such as pricing, checkout, developer, and help experiences are tighter: bordered cards, rate tables, checkout modules, code-like surfaces, and direct task language.

Key Characteristics:

- Deep trust palette built from {colors.primary}, {colors.deepNavy}, {colors.slateNavy}, and white.
- Electric action system using {colors.accent} for links, hover states, and payment affordances.
- Pill commerce CTAs using {rounded.pill}, especially for signup, checkout, and send-money actions.
- Large geometric typography with tight negative tracking in {typography.display} and {typography.hero}.
- Modular product storytelling for pay, send, save, checkout, rewards, pricing, and merchant tools.
- Minimal elevation: hierarchy comes from scale, borders, spacing, and contrast before shadow.
- Checkout-specific yellow reserved for wallet and payment-button moments.

## Colors

### Primary & Action

**primary - {colors.primary}**\
The core PayPal trust blue. Use it for the main CTA, selected tab states, brand marks, and moments where the interface needs to feel secure and official.

**accent - {colors.accent}**\
The brighter action blue. Use it for links, supporting actions, hover emphasis, and digital product details that need energy without becoming decorative.

**accentHover - {colors.accentHover}**\
A darker interaction blue for hover and focus feedback. It keeps interactive states inside PayPal's trusted blue family.

**accentPressed - {colors.accentPressed}**\
The deepest active blue. Use for pressed primary buttons, selected pills, and compact high-contrast states.

**checkoutYellow - {colors.checkoutYellow}**\
A payment-button color, not a general brand accent. Use it only for PayPal checkout, wallet button, or payment confirmation modules.

### Surfaces

**canvas - {colors.canvas}**\
The dominant page field. PayPal uses white to communicate clarity and reduce friction in financial tasks.

**surface - {colors.surface}**\
The default card and form surface. It should remain clean, bordered, and calm.

**surfaceAlt / offWhite - {colors.surfaceAlt}**\
A warm marketing band used to soften long pages. It prevents the site from feeling sterile while preserving trust.

**footerBg - {colors.footerBg}**\
The light gray footer field for legal, utility, and secondary navigation.

**codeCanvas - {colors.codeCanvas}**\
A neutral technical background for snippets and developer-adjacent modules.

### Neutrals & Text

**ink - {colors.ink}**\
The strongest text color. It is nearly navy, so even black-looking type stays tied to the brand.

**body - {colors.body}**\
Default paragraph copy for product explanations, pricing notes, and feature descriptions.

**muted - {colors.muted}**\
Secondary copy, metadata, helper text, card captions, and disclosure support.

**border - {colors.border}**\
Default line color for cards, dividers, pricing rows, and navigation separation.

**borderStrong - {colors.borderStrong}**\
Use for input outlines, active table divisions, and places where a subtle border would disappear.

### Semantic

**success - {colors.success}**\
Used for verified, completed, and successful money movement states.

**warning - {colors.warning}**\
Used sparingly for account review, timing, or attention states.

**error - {colors.error}**\
Used for failed payment, validation, and destructive feedback.

### Brand-specific signatures

**darkBlue - {colors.darkBlue}**\
Official PayPal blue, overlapping with {colors.primary}. Use when the color is acting as a direct brand identifier.

**middleBlue - {colors.middleBlue}**\
Official middle/action blue, useful for links and brand-mark echoes.

**rightBlue - {colors.rightBlue}**\
Legacy light-blue logo accent. Use sparingly for mark-inspired details.

**deepNavy - {colors.deepNavy}**\
Enterprise-leaning dark blue for serious product, pricing, and platform sections.

**venmoBlue - {colors.venmoBlue}**\
Used only when referencing wallet choice, Venmo, or multi-payment contexts.

**payLaterGold - {colors.payLaterGold}**\
Reserved for Pay Later badges and promotional payment moments.

## Typography

PayPal typography should feel geometric, compact, direct, and trustworthy. Use PayPal Pro for brand display where available, PayPal Open for product UI where available, and Helvetica Neue or Arial as fallbacks.

| Level | Size | Weight | Line-height | Letter-spacing | Use |
|---|---:|---:|---:|---:|---|
| display | 76px | 700 | 0.95 | -0.045em | Top marketing statements |
| hero | 64px | 700 | 1.0 | -0.04em | Hero claims |
| headline-lg | 48px | 700 | 1.08 | -0.035em | Major section heads |
| title-lg | 36px | 700 | 1.14 | -0.025em | Product panels |
| title-md | 28px | 700 | 1.2 | -0.018em | Cards and modules |
| title-sm | 22px | 700 | 1.27 | -0.01em | Small cards |
| body | 18px | 400 | 1.5 | 0em | Marketing copy |
| body-sm | 16px | 400 | 1.5 | 0em | UI copy |
| label | 14px | 700 | 1.25 | 0.005em | Labels and badges |
| button | 16px | 700 | 1.25 | 0em | CTAs |
| caption | 14px | 400 | 1.45 | 0em | Metadata |
| legal | 12px | 400 | 1.45 | 0em | Disclosures |
| pricing-display | 56px | 700 | 0.95 | -0.045em | Rates and numerals |
| code | 14px | 400 | 1.55 | 0em | Developer snippets |

Principles:

- Track large headlines tightly so claims feel compact and decisive.
- Use bold weight for verbs, rates, section titles, and product groups.
- Keep body copy plain and readable; do not introduce expressive serif styles.
- Let numerals anchor pricing and proof points.
- Use direct commerce verbs: Pay, Send, Save, Get paid, Get growing.

## Layout

The spacing system uses a practical 4px/8px base with larger jumps for marketing sections. Small controls use {spacing.sm} and {spacing.md}; cards use {spacing.lg} to {spacing.xl}; hero bands and full sections use {spacing.2xl} to {spacing.section}.

Recommended maximum widths are 1200-1280px for marketing containers, 960-1120px for content and developer pages, and 720-840px for focused forms. Use a 12-column desktop grid, 6-column tablet grid, and single-column mobile stack.

Whitespace is part of the trust signal. Do not crowd financial information. Separate rates, disclosures, account states, and actions with clear blocks so the next step is obvious without decoration.

## Elevation & Depth

PayPal is mostly flat. Cards use borders, radius, padding, and color contrast before shadows. The standard low elevation is `0 2px 12px rgba(0, 20, 53, 0.12)` for checkout-like modules, while floating navigation can use `0 12px 32px rgba(0, 20, 53, 0.12)`.

Avoid glass, glows, glossy gradients, and heavy ecommerce shadows. Depth should be functional: dropdowns float, checkout cards feel slightly tactile, and pricing modules remain clear without theatrical effects.

## Components

### Buttons

**button-primary**\
The conversion CTA. It uses {colors.primary}, {colors.on-primary}, {typography.button}, and {rounded.pill}. Use for Sign Up, Get Started, Send Money, and primary checkout paths.

**button-primary-active**\
Pressed state for primary actions. The deeper {colors.accentPressed} keeps the action serious and clear.

**button-secondary**\
Outlined action for secondary paths such as Learn More, Contact Sales, or Discover Checkout. Match primary height and radius.

**button-secondary-on-dark**\
Transparent outlined button for dark blue sections. It should preserve the pill shape and white border.

**button-ghost**\
Low-emphasis navigation and contextual action. Use in nav bars, cards, and utility rows.

**button-checkout-yellow**\
Reserved for payment-button modules. Do not use it as a general marketing CTA.

### Cards & Containers

**hero-band**\
A large off-white or white rounded field with oversized type and a direct action. Use for page introductions and major product stories.

**feature-card**\
Default modular card for benefits, rewards, merchant features, and integration choices. It uses border containment and spacious padding.

**pricing-card**\
Rate-led card for PayPal, Venmo, Pay Later, card processing, POS, invoicing, and virtual terminal pricing. Pair big numerals with concise explanatory copy.

**checkout-module**\
Slightly elevated wallet or checkout container. Use for payment choice, card summary, confirmation, and Fastlane-like checkout previews.

**code-block**\
Developer and integration snippets. Keep neutral, readable, and compact.

### Inputs & Forms

**input**\
White field, strong border, 16px text, and {rounded.md}. It should feel secure, utilitarian, and calm.

**input-focus**\
Use {colors.accent} as the focus border. Focus treatment must be obvious for checkout and account flows.

### Navigation

**nav-shell**\
White navigation with PayPal mark, audience paths, product categories, login, and signup. Keep it compact but not cramped.

### Pricing

**pricing-rate**\
Large, tightly tracked numerals. Rates should be visually dominant but always paired with clear transaction details.

**tab-pill / tab-pill-active**\
Used for product groups, pricing filters, and business-stage selection. Active pills use {colors.primary}.

### Signature Components

**Pay / Send / Save rhythm**\
Consumer pages organize value around short verb-led stories. Use this pattern for consumer product sections.

**PayPal Open platform grouping**\
Business pages cluster capabilities into commerce groups such as get paid, get growing, and get ahead.

**Checkout integration selector**\
Developer and merchant content uses integration cards, payment-option comparisons, and checkout modules. Keep choices scannable and action-led.

## Do's and Don'ts

Do:

- Use {colors.primary} for the main CTA and {colors.accent} for links.
- Set major page claims in {typography.hero} or {typography.headline-lg}.
- Use {rounded.pill} for commerce CTAs.
- Reserve {colors.checkoutYellow} for wallet and checkout modules.
- Use {colors.surfaceAlt} for warm story bands.
- Keep pricing numerals in {typography.pricing-display}.
- Use borders before shadows for cards.
- Build business pages around product capability groups.

Don't:

- Do not use {colors.checkoutYellow} as a generic hero color.
- Do not replace PayPal blues with arbitrary fintech greens or neons.
- Do not square off main CTAs.
- Do not add glassmorphism, glow, or glossy gradients.
- Do not make legal or pricing disclosures smaller than {typography.legal}.
- Do not treat all blues as interchangeable.
- Do not use playful rounded display fonts.
- Do not bury checkout actions inside decorative panels.

## Responsive Behavior

| Breakpoint | Width | Strategy |
|---|---:|---|
| xs | 0-479px | Single column, full-width CTAs |
| sm | 480-767px | Stacked cards, 24px side padding |
| md | 768-1023px | Two-card rows, simplified nav |
| lg | 1024-1279px | Desktop nav, 12-column grid |
| xl | 1280px+ | Max-width containers |
| xxl | 1440px+ | Increase margins, not text measure |

Touch targets should be at least 44px high. Primary mobile CTAs can be 48-52px. Button rows should stack when two actions cannot maintain comfortable width.

Collapsing strategy:

- Collapse mega navigation into audience and product groups.
- Stack pricing cards before rate text wraps awkwardly.
- Step hero type down from {typography.display} to {typography.title-lg} on small screens.
- Move secondary CTAs below primary CTAs on mobile.
- Keep disclosures close to related rates and actions.

Images and mockups should crop gently and remain secondary to action clarity. Rounded image containers should align with {rounded.xl}.

## Iteration Guide

1. Check the first screen for one clear claim in {typography.hero} and one primary CTA using {colors.primary}.
2. Verify main CTAs use {rounded.pill}; only forms and code blocks should use smaller radii.
3. Audit color roles: links are {colors.accent}, body text is {colors.body}, primary text is {colors.ink}.
4. Review cards: default cards use borders and padding before shadows.
5. Inspect pricing: rates use {typography.pricing-display} and transaction details are nearby.
6. Confirm checkout modules: yellow appears only as {colors.checkoutYellow}.
7. Test mobile: CTAs are at least 44px tall and stacked content preserves disclosure proximity.
8. Compare product IA: consumer pages should follow pay/send/save; business pages should follow capability groups.

## Known Gaps

This extraction reviewed PayPal's public US homepage, pricing and fees pages, business patterns, developer checkout references, and public coverage of the 2024 identity refresh.

Exact live CSS values for every marketing component were not fully exposed. Typography sizes, spacing, shadows, and radii are conservative approximations based on observed web patterns, public brand references, and checkout/developer UI conventions.

PayPal bridges legacy checkout button conventions, older brand color documentation, and the 2024 Pentagram-era identity. This file prioritizes live PayPal.com patterns and scopes legacy colors to specific roles.
