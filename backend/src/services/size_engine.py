from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class SizeEngine:
    """
    Deterministic size recommendation engine.
    Calculates size based on user measurements and product garment specs.
    LLM must NOT perform these calculations.
    """

    def recommend_size(self, user_profile: Dict[str, Any], product_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict the best size for a user.
        
        Args:
            user_profile: {
                "height": int (cm),
                "weight": int (kg),
                "build_type": str ("slim", "average", "athletic", "heavier"),
                "fit_preference": str ("tight", "true", "loose")
            }
            product_data: {
                "fit_type": str ("slim", "relaxed", "cropped", "oversized"),
                "stretch_level": int (0 to 3),
                "size_chart": dict (Optional mapping of sizes to measurements)
            }
        """
        try:
            height = user_profile.get("height", 175)
            weight = user_profile.get("weight", 75)
            build = user_profile.get("build_type", "average")
            preference = user_profile.get("fit_preference", "true")
            
            fit_type = product_data.get("fit_type", "relaxed")
            stretch = product_data.get("stretch_level", 1)
            
            # 1. Base Size Estimation (Weight-based anchor)
            if weight < 60: base = "S"
            elif 60 <= weight < 75: base = "M"
            elif 75 <= weight < 90: base = "L"
            elif 90 <= weight < 105: base = "XL"
            else: base = "XXL"
            
            # 2. Adjustments per industry standard physics
            # build_shift: how many sizes to move based on body type
            build_shift = {"slim": -1, "average": 0, "athletic": 0, "heavier": 1}
            # pref_shift: how many sizes to move based on wearer's desire
            pref_shift = {"tight": -1, "true": 0, "loose": 1}
            # fit_shift: how the garment's own cut affects fit (slim fit needs sizing up)
            fit_shift = {"slim": 1, "relaxed": -1, "cropped": 0, "oversized": -1}
            
            total_shift = build_shift.get(build, 0) + pref_shift.get(preference, 0) + fit_shift.get(fit_type, 0)
            
            # 3. Final Size Calculation
            sizes = ["XS", "S", "M", "L", "XL", "XXL"]
            base_idx = sizes.index(base)
            final_idx = max(0, min(len(sizes)-1, base_idx + total_shift))
            recommended_size = sizes[final_idx]
            
            # 4. Confidence Score (Stretch improves probability of fit)
            # High stretch (3) = High probability (0.95), No stretch (0) = Lower probability (0.75)
            probability_score = 0.75 + (stretch * 0.05)
            if preference == "true": probability_score += 0.05
            
            reasoning = (
                f"Based on a {build} build and {height}cm height, we anchored at {base}. "
                f"Adjusted for {fit_type} fit and {preference} preference. "
                f"{'High stretch fabric' if stretch > 1 else 'Low stretch fabric'} factored into fit flexibility."
            )
            
            return {
                "recommended_size": recommended_size,
                "probability_score": round(probability_score, 2),
                "reasoning_summary": reasoning
            }
            
        except Exception as e:
            logger.error(f"Size engine error: {e}")
            return {
                "recommended_size": "M", # Safe default
                "probability_score": 0.5,
                "reasoning_summary": "Defaulted due to processing error."
            }

size_engine = SizeEngine()
