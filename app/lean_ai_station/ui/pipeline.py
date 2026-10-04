"""Orchestrates one dossier: translate (Goedel-Formalizer) → prove (Goedel-Prover) → explain (Qwen3), switching the
loaded model for each stage, and handles follow-up requests in the dossier's thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .. import leancheck
from ..dossiers import Dossier, DossierStore, Library, closure, route, with_lemmas
from ..errors import friendly
from ..i18n import _, language

STAGES = ("statement", "proof", "explanation")
ROLE = {"understand": "explainer", "statement": "formalizer", "proof": "prover", "explanation": "explainer"}


class Pipeline(QObject):
    changed = Signal()            # the current dossier changed (thread, versions)
    listChanged = Signal()        # dossiers were created / renamed / deleted
    stageChanged = Signal(str)    # "" when idle, else statement | proof | explanation
    loading = Signal(str)         # a model is being loaded for this stage

    def __init__(self, ctx, store: DossierStore | None = None, library: Library | None = None):
        super().__init__()
        self.ctx = ctx
        self.store = store or DossierStore()
        self.library = library or Library()
        self.dossier: Dossier | None = None
        self.queue: list[tuple[str, str]] = []      # (stage, request) still to run
        self.stage = ""
        self._request = ""
        self._pending = False                       # waiting for a model to load
        ctx.formalizer.finished.connect(self._translated)
        ctx.formalizer.infraError.connect(self._infra)
        ctx.prover.finished.connect(self._proved)
        ctx.prover.infraError.connect(self._infra)
        ctx.explainer.finished.connect(self._explained)
        ctx.explainer.understood.connect(self._understood)
        ctx.explainer.infraError.connect(self._infra)
        ctx.server.failed.connect(self._load_failed)

    # ------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        return bool(self.stage) or self._pending

    def _save(self):
        if self.dossier:
            self.store.save(self.dossier)
            self.ctx.session["dossier"] = self.dossier.id
            self.ctx.save_later()
        self.changed.emit()

    def _event(self, kind: str, text: str, ref: int | None = None, stage: str = ""):
        if self.dossier:
            self.dossier.add_event(kind, text, ref, stage)
            self._save()

    # ------------------------------------------------------------ dossiers
    def open(self, did: str) -> bool:
        if self.busy:
            return False
        d = self.store.load(did)
        if d is None:
            return False
        self.dossier = d
        self.ctx.session["dossier"] = d.id
        self.changed.emit()
        return True

    def new(self, problem: str = "", title: str = "") -> Dossier:
        ws = self.ctx.workspace()
        self.dossier = self.store.new(problem, ws.key if ws else "", title)
        self._save()
        self.listChanged.emit()
        return self.dossier

    def rename(self, title: str):
        if self.dossier and title.strip():
            self.dossier.title = title.strip()
            self._save()
            self.listChanged.emit()

    def delete_current(self) -> str | None:
        if not self.dossier or self.busy:
            return None
        did = self.dossier.id
        self.store.delete(did)
        self.dossier = None
        self.listChanged.emit()
        self.changed.emit()
        return did

    def undelete(self, did: str):
        if self.store.undelete(did):
            self.listChanged.emit()
            self.open(did)

    # ------------------------------------------------------------ entry points
    def start(self, problem: str):
        """New dossier from a plain-language problem: translate → prove → explain, without stopping."""
        d = self.new(problem)
        self._event("user", problem, stage="auto")
        self._run([("understand", ""), ("statement", ""), ("proof", ""), ("explanation", "")])
        return d

    def start_with_statement(self, statement: str, problem: str = "", title: str = ""):
        """New dossier from a ready Lean statement (examples, .lean files): prove → explain."""
        self.new(problem, title)
        if problem:
            self._event("user", problem, stage="auto")
        idx = self.dossier.add_statement(leancheck.prepare_statement(statement), source="user")
        self._event("statement", _("Énoncé Lean fourni."), ref=idx)
        self._run([("proof", ""), ("explanation", "")])

    def request(self, text: str, stage: str = "auto") -> str:
        """Follow-up request in the thread. Returns the stage that will be redone."""
        d = self.dossier
        if d is None:
            self.start(text)
            return "statement"
        if not d.statement:                                # nothing translated yet: treat as the problem itself
            d.problem = (d.problem + "\n" + text).strip() if d.problem else text
            self._event("user", text, stage="statement")
            self._run([("understand", ""), ("statement", ""), ("proof", ""), ("explanation", "")])
            return "statement"
        if stage == "auto":
            stage = route(text, d.proof_is_current, bool(d.explanation))
        self._event("user", text, stage=stage)
        plan = {"statement": [("statement", text), ("proof", ""), ("explanation", "")],
                "proof": [("proof", text), ("explanation", "")],
                "explanation": [("explanation", text)]}[stage]
        if stage == "explanation" and not d.proof_is_current:
            plan = [("proof", ""), ("explanation", text)]
        self._run(plan)
        return stage

    def set_statement(self, code: str):
        """The user edited the Lean statement by hand."""
        if not self.dossier:
            self.new(title=_("Énoncé écrit à la main"))
        idx = self.dossier.add_statement(leancheck.prepare_statement(code), source="user")
        self._event("statement", _("Énoncé modifié à la main."), ref=idx)

    def prove_current(self):
        if self.dossier and self.dossier.statement:
            self._run([("proof", ""), ("explanation", "")])

    def explain_current(self, request: str = ""):
        if self.dossier and self.dossier.proof:
            self._run([("explanation", request)])

    def restore(self, kind: str, index: int):
        if self.dossier and not self.busy:
            self.dossier.restore(kind, index)
            label = {"statement": _("énoncé"), "proof": _("preuve"), "explanation": _("explication")}[kind]
            self._event("info", _("Retour à la version {n} de la {what}.").format(n=index + 1, what=label))

    def cancel(self):
        if not self.busy:
            return
        self.queue = []
        if self._pending:
            self._pending = False
            self.ctx._after_ready.clear()
        for svc in (self.ctx.formalizer, self.ctx.prover, self.ctx.explainer):
            if svc.running:
                svc.cancel()
        self._idle()
        self._event("info", _("Arrêté à votre demande."))

    # ------------------------------------------------------------ scheduler
    def _run(self, plan: list[tuple[str, str]]):
        if self.busy:
            return
        self.queue = list(plan)
        self._next()

    def _idle(self):
        self.stage = ""
        self.stageChanged.emit("")

    def _next(self):
        if not self.queue or not self.dossier:
            self._idle()
            return
        stage, req = self.queue.pop(0)
        role = ROLE[stage]
        if stage == "understand" and (self.ctx.role_model(role) is None or self.ctx.role_model("formalizer") is None):
            self._next()        # optional step (the formalizer then gets the user's own words, or reports it is missing)
            return
        if self.ctx.role_model(role) is None:
            self.queue = []
            self._idle()
            self.ctx.banner.emit(friendly({"formalizer": "no_formalizer", "explainer": "no_explainer"}.get(role, "server_down")), "")
            self._event("error", _("Étape impossible : le modèle nécessaire n'est pas installé (voir « Modèles »)."))
            return
        ws = self.ctx.workspace()
        if ws is None or ws.check():
            self.queue = []
            self._idle()
            self.ctx.banner.emit(friendly("workspace"), "\n".join(ws.problems) if ws else "")
            return
        self.stage, self._request, self._ws = stage, req, ws
        self.stageChanged.emit(stage)
        target = self.ctx.role_model(role)
        if not (self.ctx.server.state == "ready" and self.ctx.server.model_path == target):
            self._pending = True
            self.loading.emit(stage)
        self.ctx.ensure_model(self._go, role=role)

    def _go(self):
        if not self._pending and not self.stage:
            return
        self._pending = False
        d, s, req = self.dossier, self.ctx.settings, self._request
        if self.stage == "understand":
            self.ctx.explainer.understand(d.problem, s.profile)
        elif self.stage == "statement":
            previous = d.statement if req else ""
            self.ctx.formalizer.start(d.understood or d.problem, self._ws, s.translate_attempts, s.compile_timeout_s, name=d.theorem,
                                      profile=s.profile, previous=previous, request=req)
        elif self.stage == "proof":
            used = [e for e in closure(self.library, self.library.relevant(d.statement, self._ws.key,
                                                                         exclude=leancheck.declared_names(d.statement)))]
            d.lemmas_used = [e.name for e in used]
            statement = with_lemmas(d.statement, used)
            if used:
                self._event("info", _("Résultats de votre bibliothèque proposés à l'IA : {names}.").format(
                    names=", ".join(e.title for e in used)))
            refine = (d.proof, req) if (req and d.proof_is_current) else None
            try:
                self.ctx.prover.start(statement, self._ws, s.prove_attempts, s.sampling, s.compile_timeout_s,
                                      self.ctx.server.plan.ctx if self.ctx.server.plan else s.server.ctx_size, refine=refine)
            except leancheck.StatementError as e:
                self._fail(str(e))
        elif self.stage == "explanation":
            prev = d.explanation if req else ""
            self.ctx.explainer.start(d.proof, d.problem, language(), s.profile, prev, req)

    def _fail(self, text: str):
        self.queue = []
        self._idle()
        self._event("error", text)

    def _load_failed(self, *_a):
        if self._pending:
            self._pending = False
            self._fail(_("Le modèle n'a pas pu être chargé (voir le message en haut)."))

    def _infra(self, kind: str, details: str):
        if self.stage:
            self._fail(_("Interrompu : le moteur d'IA ou Lean ne répond plus. Voir le message en haut, puis renvoyez votre demande."))

    # ------------------------------------------------------------ results
    def _understood(self, ok: bool, text: str):
        if self.stage != "understand":
            return
        if ok and leancheck.is_unknown(text):
            self._fail(_("Je ne reconnais pas d'énoncé mathématique dans votre demande. Écrivez le résultat à prouver "
                         "avec ses hypothèses, par exemple : « toute famille libre d'un espace vectoriel de dimension "
                         "finie se complète en une base »."))
            return
        if ok and text.strip():
            self.dossier.understood = text.strip()
            self._event("understood", _("Problème compris ainsi (c'est ce texte que l'IA traduit en Lean) :") + "\n"
                        + text.strip())
        QTimer.singleShot(0, self._next)

    def _translated(self, ok: bool, summary: str):
        if self.stage != "statement":
            return
        f, d = self.ctx.formalizer, self.dossier
        if f.was_cancelled or not f.statement:
            self._fail(_("Pas d'énoncé produit. Reformulez le problème (plus précis, avec les hypothèses) "
                         "ou écrivez l'énoncé Lean vous-même dans l'onglet « Énoncé Lean ».") if not f.statement
                       else _("Traduction arrêtée."))
            return
        idx = d.add_statement(f.statement, ok=ok, note=self._request)
        if ok:
            self._event("statement", _("L'IA a traduit le problème en Lean et Lean accepte l'énoncé. "
                                       "Relisez-le : la preuve portera exactement sur ce texte."), ref=idx)
            if self.ctx.settings.pause_after_translation:
                self.queue = []
                self._event("info", _("Pause : relisez l'énoncé, puis cliquez sur « Prouver cet énoncé »."))
        else:
            self.queue = []
            self._event("error", _("Lean refuse la traduction. Corrigez l'énoncé dans l'onglet « Énoncé Lean », "
                                   "ou demandez une correction ci-dessous."), ref=idx)
        QTimer.singleShot(0, self._next)

    def _proved(self, ok: bool, summary: str):
        if self.stage != "proof":
            return
        p, d = self.ctx.prover, self.dossier
        if not ok:
            if not p.was_cancelled:
                self._fail(_("Aucune preuve trouvée après {n} essais. Essayez : « autre méthode », un énoncé plus "
                             "simple, ou plus d'essais (⚙ Options).").format(n=len(p.attempts)))
            return
        idx = d.add_proof(p.final_code, note=self._request)
        a = p.attempts[-1]
        self._event("proof", _("Preuve trouvée et vérifiée par Lean (essai {i}, {s:.0f} s).").format(
            i=len(p.attempts), s=sum(x.gen_seconds + x.compile_seconds for x in p.attempts)), ref=idx)
        entry = self.library.add_proof(p.final_code, d.title, self._ws.key, d.id, used=d.lemmas_used)
        if entry:
            self._event("info", _("Ajouté à votre bibliothèque sous le nom « {name} » : réutilisable dans vos "
                                  "prochaines preuves.").format(name=entry.name))
        QTimer.singleShot(0, self._next)

    def _explained(self, ok: bool, text: str, summary: str):
        if self.stage != "explanation":
            return
        if not ok:
            self._fail(summary)
            return
        idx = self.dossier.add_explanation(text, note=self._request)
        self._event("explanation", _("Explication rédigée par l'IA (la preuve Lean fait foi)."), ref=idx)
        QTimer.singleShot(0, self._next)
