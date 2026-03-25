<!-- GSD:project-start source:PROJECT.md -->
## Project

**Smart Trolley Checkout System**

A self-contained smart trolley checkout system running on a Raspberry Pi 4B. Customers walk around a store, scan products with the Pi's camera, see live cart totals on a TFT display, pay via UPI QR code, and receive a Bluetooth-printed receipt — all without a cashier. A separate Next.js admin panel lets the shop owner manage the product catalog via a cloud-hosted Neon PostgreSQL database.

**Core Value:** A customer can scan items, pay via QR code, and get a printed receipt entirely on the trolley — no cashier needed.

### Constraints

- **Hardware**: Pi 4B ARM64 — runtime must be tested on Pi; some packages (picamera2, luma.lcd) are Pi-only
- **Display**: ST7735 TFT, 128×160 or 160×128 px — very limited screen real estate, UI must be simple
- **Database**: Neon free tier — connection pooling URL must be used; avoid long-lived connections
- **Stack (web)**: Next.js 15 App Router, TypeScript, Prisma, Tailwind, shadcn/ui — no REST API needed (Server Actions)
- **Stack (pi)**: picamera2, pyzbar, luma.lcd (st7735 driver), hx711, python-escpos, psycopg2-binary, python-dotenv, qrcode, Pillow
<!-- GSD:project-end -->

<!-- GSD:stack-start source:STACK.md -->
## Technology Stack

Technology stack not yet documented. Will populate after codebase mapping or first phase.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
