# UI motion implementation

The app uses local CSS/WAAPI adaptations of Material motion, retaining native controls,
form validation, focus, and synchronous `dialog.close()` semantics. It does not import
Material's framework runtime or claim pixel-identical reproduction of every demo.

## Sources reviewed

- M3 component index: https://m3.material.io/components/
- M3 physics system: https://m3.material.io/styles/motion
- Expressive springs (Android Open Source Project): https://raw.githubusercontent.com/androidx/androidx/androidx-main/compose/material3/material3/src/commonMain/kotlin/androidx/compose/material3/tokens/ExpressiveMotionTokens.kt
- Web easing and animation lifecycle: https://github.com/material-components/material-web/blob/main/internal/motion/animation.ts
- Dialog sequence: https://github.com/material-components/material-web/blob/main/dialog/internal/animations.ts
- Menu surface and item sequence: https://github.com/material-components/material-web/blob/main/menu/internal/menu.ts
- Ripple state machine: https://github.com/material-components/material-web/blob/main/ripple/internal/ripple.ts
- Switch handle motion: https://github.com/material-components/material-web/blob/main/switch/internal/_handle.scss
- Checkbox: https://github.com/material-components/material-web/blob/main/checkbox/internal/_checkbox.scss
- Connected groups: https://github.com/material-components/material-components-android/blob/master/docs/components/ButtonGroup.md

The live JavaScript-rendered Specs pages were also inspected for buttons, button
groups, switches, checkboxes, radios, dialogs, menus, text fields, search, navigation
drawers, cards, and chips. Notes are mapped to the controls that actually exist in
this app; unused FAB/carousel/slider components were not introduced.

## Mapping

| Control | Implementation |
| --- | --- |
| Buttons, icon buttons, navigation items | Hover/focus state layer; pointer-held ripple; keyboard ripple; delayed touch intent; release fade |
| Connected single selection | Selected pill shape, press expansion, spatial spring; effects spring for color |
| Switches | Separate track color, handle translation, pressed handle growth |
| Checkboxes | State layer, fill, then check-mark reveal |
| Text fields and search | Label/notch movement, focused outline, icon/clear cross-fade |
| Select menus | Anchor direction, clipping instead of text scaling, visible-item staggering, spatial spring reveal/shrink and independent effects spring opacity |
| Dialogs | Native top-layer entry/exit; translate and scrim, delayed headline/content/actions |
| Navigation drawer | Spatial spring on panel, independent opacity on scrim |
| Page navigation | Fade-through / small scale change only when a hidden page becomes visible |
| Details / expandable lists | Intrinsic block-size transition, content visibility and indicator rotation |
| Chips / tags and actionable list rows | Effects transitions; no decorative hover movement on static cards |
| Registration/check-in button | Deliberately **not** a spring: the original 800ms/600ms `cubic-bezier(.23,1,.32,1)` ease-out drives the arrow/circle sweep and state colors |

`motion.js` samples the analytic unit-mass spring equation using the official expressive
stiffness and damping tokens into CSS `linear()` easing. CSS durations are chosen from
position and velocity settling, so the timing follows the spring rather than arbitrary
per-control easing. Native Web implementations above also use fixed easing/duration;
the ripple retains its documented press timing, while menus/dialog movement now use the shared expressive spring profiles.

The registration/check-in button is the one intentional exception. Its arrow and
expanding circle are a single long sweep rather than a spatial retarget, and an
overshooting spring visibly wobbles the 220px circle. It therefore keeps its original
traditional ease-out curve (`--animated-button-ease`, 800ms movement / 600ms state),
defined in `motion.css` so it still collapses under the reduced-motion policy.

## Boundaries

- Browser-native date/time pickers, file pickers, tooltips, and scrollbars cannot have
  their internal animation replaced by page CSS. The app retains those native surfaces.
- The dialog surface uses native discrete display/overlay transitions, rather than
  Material Web's shadow-DOM pseudo-container height animation. Content is not scaled.
- The menu surface uses clipping for reveal/shrink, instead of changing the real height.
- Expandable content animation depends on `::details-content` / intrinsic-size support;
  otherwise the disclosure still works without an animated height.
- Existing OS capability policy chooses `data-motion`; unknown/older systems receive
  immediate state changes. In-flight app WAAPI animations are canceled on downgrade.
- Typography, color palette, card radius, data, and backend behavior are unchanged.

## Device-side execution

All curve generation, animation state and rendering run in the browser. Six sampled
spring profiles are built once per document, then reused by CSS and WAAPI. This adds
no server endpoint, animation polling, or per-frame server computation. Application
data requests remain unchanged.

WAAPI channels keep geometry and effects independent. Interrupted animations restart
from their rendered value rather than snapping to the original start; this preserves
position, but does not claim exact velocity continuity across arbitrary keyframes.
Menus measure visible rows before writes and animate only those rows. Transform and
opacity are preferred; clipping and intrinsic-height/shape transitions may still use
local paint/layout work depending on the browser. No permanent GPU layer promotion
or per-frame JavaScript rendering loop is introduced. Hidden documents do not start
new app WAAPI animations; the existing reduced-motion OS policy remains in force.

## Theme reveal

A theme-toggle click in full-motion mode uses the same-document View Transition API.
The browser captures the outgoing view, applies the incoming theme to the existing
DOM, and renders the incoming snapshot. The incoming snapshot has a zero-radius
circular clip before its first frame, then reveals outward from the toggle center.
The outgoing snapshot remains opaque beneath it. Transient component transitions
are suppressed during capture so the incoming screenshot already has final colors.

After `ViewTransition.finished`, the reveal animation is canceled, temporary CSS
variables/attributes and animation references are removed, and buttons are unlocked.
Snapshot lifetime and actual GPU/heap memory reclamation belong to the browser;
the app does not clone a second DOM or force garbage collection. Resize, backgrounding,
and switching to reduced motion skip the reveal while retaining the new theme.
Unsupported browsers switch directly. This is entirely device-side.

API lifecycle/reference: https://developer.chrome.com/docs/web-platform/view-transitions/same-document
