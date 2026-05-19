# Detail Image Single Reupload Replace

## Request

In the media product detail-image editor, when exactly one detail image is selected, the operator can upload one new image file to replace that selected image.

## Design

- Reuse the existing detail-image selection toolbar and local browser upload flow.
- Show a replace action only when one selected detail image is still present in the current list.
- Reserve a replacement upload target through a dedicated backend endpoint so replacement is not blocked when the current language is already at the static/GIF image limit.
- Complete replacement through a dedicated endpoint that validates the uploaded object exists, verifies the selected image belongs to the product, updates the existing detail-image row in place, and best-effort deletes the old object.
- Preserve the selected image's position by keeping its row and `sort_order`; mark the replacement as `origin_type="manual"` and clear translation provenance fields.

## Verification

- Unit-test replacement upload reservation and completion service helpers.
- Route-test that replacement endpoints delegate to service helpers.
- Static-test that the selection toolbar exposes the single-image replace action and calls the replacement endpoints.
