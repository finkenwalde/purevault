# Purevault sequential feature roadmap

The following features are approved for implementation and review one at a time. Completing one item does not authorize starting the next item in the same implementation turn.

## 1. Automatic inactivity sign-out

Implemented: authenticated sessions expire after 10 minutes without meaningful keyboard, pointer, or touch activity. Background polling does not extend the session, and expiry does not change vault grants.

## 2. Activity history

Add an owner-scoped timeline covering unlock requests, cancellation, reveal, and relock. It must contain no passwords, define retention explicitly, and state honestly that a host administrator remains inside the trust boundary.

## 3. Lock all

Add one intentional action that cancels all pending unlock requests and relocks all currently available entries. Clearly explain its scope and do not delete entries.

## 4. Search and status filtering

Add title search and filters for Locked, Waiting, and Available entries.

## 5. Password generator

Add a cryptographically secure generator for entry creation with configurable length and character categories. Give the user a deliberate opportunity to apply or copy the generated value before saving and locking it. Do not add a plaintext endpoint capable of exposing existing locked entries.

## 6. Recoverable Trash

Permanent deletion is available from the vault and requires explicit confirmation and the current master password.

A future Trash workflow would hold entries for 30 days before permanent deletion. Moving an entry to Trash would cancel its unlock grant and block reveals. Restoring an entry would return it locked with its full unlock delay.

There is no Archive feature in this roadmap.
