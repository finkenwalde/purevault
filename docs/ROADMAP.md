# Purevault sequential feature roadmap

The following features are approved for implementation and review one at a time. Completing one item does not authorize starting the next item in the same implementation turn.

## 1. Automatic inactivity sign-out

Implemented: authenticated sessions expire after 10 minutes without meaningful keyboard, pointer, or touch activity. Background polling does not extend the session, and expiry does not change vault grants or queued mail.

## 2. Activity history

Add an owner-scoped timeline covering unlock requests and reasons, cancellation, reveal, relock, partner and email-configuration changes, and email delivery status. It must contain no passwords, define retention explicitly, and state honestly that a host administrator remains inside the trust boundary.

## 3. Lock all

Add one intentional action that cancels all pending unlock requests and relocks all currently available entries. Clearly explain its scope and do not delete entries.

## 4. Search and status filtering

Add title search and filters for Locked, Waiting, and Available entries.

## 5. Password generator

Add a cryptographically secure generator for entry creation with configurable length and character categories. Give the user a deliberate opportunity to apply or copy the generated value before saving and locking it. Do not add a plaintext endpoint capable of exposing existing locked entries.

## 6. Recoverable Trash and deletion

Add a simple Trash workflow with automatic permanent deletion after 30 days. Moving an entry to Trash cancels its pending grant; trashed entries cannot be unlocked or revealed. Restoring an entry returns it locked and requires a fresh full delay. Permanent deletion requires explicit confirmation and the current master password. Queued accountability notices remain independent of entry deletion. Safe purge scheduling and retention details must be finalized during this item.

There is no Archive feature in this roadmap.
