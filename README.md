# Grottle™ skins

Submit a Grottle™ gauge skin for listing on [grottle.app](https://grottle.app/#skins).

## What a skin is

A Grottle™ skin changes how the gauge dash looks. It is **pure artwork, never code**:

- SVG art (the gauge drawings, with slots Grottle™ fills in),
- a `skin.json` manifest (name, version, creator, licence, palette, layouts, share ID),
- optional bundled fonts, which must carry their licence (for example SIL OFL 1.1 with `OFL.txt`),
- locked goldens (`golden/`): reference renders that prove the skin draws the same everywhere.

No scripts of any kind. Skins that contain `.py`, `.js`, `.sh`, `.so` (or similar) files are rejected.

You can make a skin with the Grottle™ skin tools. When it passes Grottle™'s own checker, it packages a zip and gives the skin a **share ID** that looks like `g1-427ee9a0223c`.

## How to submit (no git needed)

1. Sign in to GitHub (a free account is enough).
2. Open **[Issues → New issue → Submit a skin](https://github.com/bcorn2006-art/grottle-skins/issues/new/choose)**.
3. Fill in the form: skin name, version, your creator handle, the share ID, the licence and a short description.
4. **Drag your skin zip into the "Skin zip" box.** GitHub uploads it and puts a link in the box.
5. Tick the four boxes and submit.

That's it. You don't need to fork, clone or use git.

## What gets checked

Every submission is checked on a copy of the zip, using the rules of the released Grottle™ v0.13.1 code.

**Automatic checks (run on this repo, results posted as a comment on your issue):**

- **Safe unzip:** no absolute paths, no `../` paths, no links, 200 files at most.
- **Size:** the zip is 8 MB or less, and the skin is 300 KB or less without its `golden/` folder.
- **No code:** the skin's tier is `template`, and there are no `.py`, `.js`, `.sh` or `.so` files (or other program files).
- **Share ID:** the share ID is worked out again from the files and must match the one in `skin.json` and the one you typed in the form.
- **Required files:** `skin.json` with all required fields, the art files it names, and only image/JSON files in `golden/`.
- **Art safety:** every SVG is well-formed and has no scripts, event handlers or links to outside files.
- **Locked goldens:** the goldens are present, locked, approved, and their files match their recorded checksums.
- **Fonts:** every bundled font file is there and carries its licence (for example OFL), with the licence file included.

The issue then gets the label `checks-passed` or `checks-failed`. If it failed, fix the skin and edit the issue (attach the new zip); the checks run again.

**Checks during review (run by hand with the released Grottle™ v0.13.1 code before any listing):**

- The full Grottle™ skin checker passes on every check.
- The locked goldens re-render within tolerance.
- A person checks that the name is unique and impersonates nobody, the art is fit to show, the sign reads **Grottle™**, and that you stated a licence and agreed to redistribution.

## Who approves

**BCORN approves every listing.** Passing the automatic checks does not list a skin. A skin is only listed on grottle.app after BCORN applies the `approved` label; then it is added to the site and the issue is labelled `listed`. BCORN may decline any submission (`declined`).

## No GitHub account?

Email the skin zip to **grottle@mail.grokbot.com**. Put the skin name, version, your creator handle, the share ID and the licence in the email, and say you agree to the listing terms below. Email submissions go through the same checks and the same approval: nothing is listed until BCORN approves it.

## Listing terms

By submitting a skin you agree that:

1. **Licence.** You state the licence your skin is under in the submission form. Any bundled fonts keep their own licence (for example SIL OFL 1.1), and the licence text is included in the zip.
2. **Your rights.** You made the skin, or you have the rights to share everything in it.
3. **Redistribution.** You allow the skin zip, its name, preview image, creator handle, version, share ID and licence to be published and redistributed on grottle.app, so Grottle™ users can download and install it.
4. **Names.** The skin's name and creator handle do not impersonate any person, brand or project.
5. **Takedown.** You (or BCORN) can ask for a listing to be taken down. A takedown removes the listing, index entry and zip from grottle.app, so new installs stop. **Copies already installed stay on users' machines**; Grottle™ does not remove or re-check installed skins.
6. **No guarantee of listing.** Listing is at BCORN's discretion, and a listing can be removed at any time.
