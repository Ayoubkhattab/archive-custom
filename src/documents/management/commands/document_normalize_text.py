import tqdm
from django.core.management import BaseCommand

from documents import index
from documents.management.commands.mixins import ProgressBarMixin
from documents.models import Document
from documents.text_normalization import normalize_extracted_text


class Command(ProgressBarMixin, BaseCommand):
    help = (
        "Repairs the stored text of documents consumed before Arabic text "
        "normalization existed (shaped glyphs, reversed Arabic, direction "
        "marks, tatweel) without running OCR again, and updates the search "
        "index for the documents that changed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report how many documents would change.",
        )
        self.add_argument_progress_bar_mixin(parser)

    def handle(self, *args, **options):
        self.handle_progress_bar_mixin(**options)

        changed = []
        documents = Document.objects.only("id", "content").order_by("id")
        for doc in tqdm.tqdm(documents.iterator(), disable=self.no_progress_bar):
            normalized = normalize_extracted_text(doc.content)
            if normalized != doc.content:
                changed.append(doc.pk)
                if not options["dry_run"]:
                    # update() rather than save(): this is a repair, it must
                    # not bump "modified" or fire the workflow/audit signals.
                    Document.objects.filter(pk=doc.pk).update(content=normalized)

        if options["dry_run"]:
            self.stdout.write(f"{len(changed)} document(s) would change.")
            return

        if changed:
            with index.open_index_writer() as writer:
                for doc in Document.objects.filter(pk__in=changed):
                    index.update_document(writer, doc)

        self.stdout.write(
            f"Updated the text of {len(changed)} document(s). Run "
            f'"manage.py document_llmindex update" to refresh the AI index.',
        )
