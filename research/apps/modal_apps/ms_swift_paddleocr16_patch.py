"""Compatibility patch for ms-swift 4.5.3 and PaddleOCR-VL-1.6."""

import torch

from swift.template.templates.baidu import PaddleOCR1_5Template


def _post_encode(self, model, inputs):
    if not self.is_training:
        return inputs
    base_model = self.get_base_model(model)
    input_ids = inputs["input_ids"]
    pixel_values = inputs.pop("pixel_values")
    image_grid_thw = inputs.get("image_grid_thw")
    inputs_embeds = base_model.model.language_model.embed_tokens(input_ids)
    if pixel_values is not None:
        image_embeds = base_model.model.get_image_features(
            pixel_values, image_grid_thw, return_dict=True
        ).pooler_output
        # PaddleOCR-VL-1.6 returns one projected tensor per image.  The ms-swift
        # 4.5.3 template expects the older single-tensor return value.
        if isinstance(image_embeds, (tuple, list)):
            image_embeds = torch.cat(image_embeds, dim=0)
        image_embeds = image_embeds.to(inputs_embeds.device, inputs_embeds.dtype)
        image_mask = base_model.model.get_placeholder_mask(
            input_ids, inputs_embeds=inputs_embeds, image_features=image_embeds
        )
        inputs_embeds = inputs_embeds.masked_scatter(image_mask, image_embeds)
    return {"inputs_embeds": inputs_embeds}


PaddleOCR1_5Template._post_encode = _post_encode
