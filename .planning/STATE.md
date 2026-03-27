# STATE.md — Smart Trolley Checkout System

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-25)

**Core value:** A customer can scan items, pay via QR code, and get a printed receipt entirely on the trolley — no cashier needed.
**Current focus:** Phase 1 — Monorepo Scaffold & Database Schema

## Current Phase

**Phase 1: Monorepo Scaffold & Database Schema**

Goal: Set up the full project structure, Prisma schema, and push migrations to Neon PostgreSQL.

Status: Not started

## Phase History

| Phase | Name | Status | Completed |
|-------|------|--------|-----------|
| 1 | Monorepo Scaffold & Database Schema | Pending | — |
| 2 | Pi Runtime — Idle, Scan, and Display | Pending | — |
| 3 | Pi Runtime — Checkout & UPI Payment | Pending | — |
| 4 | Pi Runtime — Bluetooth Invoice Printing | Pending | — |
| 5 | Next.js Admin Panel | Pending | — |

## Notes

- Development machine is Fedora x86_64; Pi-specific packages cannot be installed here
- Pi 4B is the target execution environment for pi_runtime/
- Neon connection string uses pooling URL (pgbouncer); avoid long-lived connections
- `.env` is git-ignored; contains DATABASE_URL, UPI_ID, SHOP_NAME, BLUETOOTH_PRINTER_MAC

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260327-001 | Add camera barcode scanner to POS dashboard | 2026-03-27 | pending | [add-camera-scanner](./quick/add-camera-scanner/) |

---
*STATE initialized: 2026-03-25*
*Last activity: 2026-03-27 - Completed quick task 260327-001: Add camera barcode scanner to POS dashboard*
