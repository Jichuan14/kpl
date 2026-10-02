"""HTTP operation pinning; legacy requests remain explicitly callable."""
from __future__ import annotations
from functools import wraps
import inspect
from typing import get_type_hints
from fastapi import HTTPException
from app.services.model_registry import bundle_scope, resolve_bundle


def pinned_model_operation(function):
    @wraps(function)
    def operation(*args, **kwargs):
        bound = inspect.signature(function).bind_partial(*args, **kwargs)
        body = bound.arguments.get('body')
        version = getattr(body, 'model_version', None) if body is not None else bound.arguments.get('model_version')
        # Direct Python legacy calls do not receive FastAPI query defaults.
        version = version if isinstance(version, str) else None
        try:
            handle = resolve_bundle(version) if version else None
            if handle and handle.manifest["promotion_status"] == "experimental":
                raise ValueError("Experimental model versions are unavailable for public tools")
            model_type = getattr(body, 'model_type', getattr(getattr(body, 'draft_state', None), 'model_type', 'personalized'))
            if handle and model_type not in {'personalized', 'stats'}:
                raise ValueError('Pinned active bundles support personalized or stats; call legacy variants without model_version')
            with bundle_scope(handle):
                result = function(*args, **kwargs)
            if handle and hasattr(result, 'data') and isinstance(result.data, dict):
                result.data = {**result.data, **handle.metadata()}
            # Coach streaming tools execute after the route function returns.
            if handle and hasattr(result, 'body_iterator'):
                original = result.body_iterator
                async def pinned_stream():
                    with bundle_scope(handle):
                        async for chunk in original:
                            import json
                            try:
                                event=json.loads(chunk)
                                if event.get("type")=="result" and isinstance(event.get("data"),dict):
                                    event["data"]={**event["data"],**handle.metadata()}
                                    chunk=json.dumps(event,ensure_ascii=False)+"\n"
                            except (TypeError,ValueError):
                                pass
                            yield chunk
                result.body_iterator = pinned_stream()
            return result
        except FileNotFoundError as error:
            raise HTTPException(404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from error
    # FastAPI must resolve postponed annotations in the original route module.
    hints = get_type_hints(function)
    signature = inspect.signature(function)
    operation.__signature__ = signature.replace(
        parameters=[p.replace(annotation=hints.get(name, p.annotation)) for name, p in signature.parameters.items()],
        return_annotation=hints.get("return", signature.return_annotation),
    )
    return operation
