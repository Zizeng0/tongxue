"""PowerPoint planning and rendering tools."""

from .presentation_writer import MAX_IMAGES, DeckContent, PPTX_FILENAME, load_template, write_presentation
from .planning import (DEPTH_GUIDANCE, NO_COVER_IMAGE, draft_presentation, enrich_notes, place_available_figures,
                       requested_slide_count, resolve_image_references)
from .design_agent import NO_IMAGES, NO_WEB_IMAGES, WEB_IMAGES, wants_illustrations, design_presentation
from .pdf_figures import SECTION_LIMIT, PdfFigure, extract_pdf_figures, find_section
