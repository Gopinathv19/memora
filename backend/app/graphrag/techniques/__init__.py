"""The five Graph RAG techniques. Each is `run(ctx) -> GraphEvidence`."""

from app.graphrag.params import Technique


def get_technique(technique: Technique):
    from app.graphrag.techniques import community, dual_level, paths, ppr, spreading

    return {
        Technique.SPREADING: spreading.run,
        Technique.PPR: ppr.run,
        Technique.DUAL_LEVEL: dual_level.run,
        Technique.PATHS: paths.run,
        Technique.COMMUNITY: community.run,
    }[Technique(technique)]
