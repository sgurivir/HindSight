#!/usr/bin/env python3
"""
Script to approximate LLM costs based on AWS Bedrock pricing.

Usage:
    python approximate_llm_cost.py -i 1000 -o 500
    python approximate_llm_cost.py --itokens 1000 --otokens 500
"""

import argparse
from typing import Dict, Tuple


# AWS Bedrock pricing as of January 2025 (USD per 1000 tokens)
# Source: https://aws.amazon.com/bedrock/pricing/
AWS_BEDROCK_PRICING: Dict[str, Dict[str, float]] = {
    # Claude Sonnet 4.5 (newest model) - DEFAULT
    "anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "input": 0.003,   # $3.00 per million input tokens
        "output": 0.015,  # $15.00 per million output tokens
    },
    # Claude 3 Opus
    "anthropic.claude-3-opus-20240229-v1:0": {
        "input": 0.015,   # $15.00 per million input tokens
        "output": 0.075,  # $75.00 per million output tokens
    },
}


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"
) -> Tuple[float, float, float]:
    """
    Calculate the approximate cost for LLM usage.
    
    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        model: AWS Bedrock model identifier
        
    Returns:
        Tuple of (input_cost, output_cost, total_cost) in USD
    """
    if model not in AWS_BEDROCK_PRICING:
        print(f"Warning: Model '{model}' not found in pricing table.")
        print(f"Available models:")
        for m in AWS_BEDROCK_PRICING.keys():
            print(f"  - {m}")
        print(f"\nUsing default model: anthropic.claude-sonnet-4-5-20250929-v1:0")
        model = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    pricing = AWS_BEDROCK_PRICING[model]
    
    # Calculate costs (pricing is per 1000 tokens)
    input_cost = (input_tokens / 1000) * pricing["input"]
    output_cost = (output_tokens / 1000) * pricing["output"]
    total_cost = input_cost + output_cost
    
    return input_cost, output_cost, total_cost


def format_cost(cost: float) -> str:
    """Format cost with appropriate precision."""
    if cost < 0.01:
        return f"${cost:.6f}"
    elif cost < 1:
        return f"${cost:.4f}"
    else:
        return f"${cost:.2f}"


def main():
    parser = argparse.ArgumentParser(
        description="Approximate LLM costs based on AWS Bedrock pricing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -i 1000 -o 500
  %(prog)s --itokens 10000 --otokens 5000
  %(prog)s -i 1000 -o 500 -m anthropic.claude-3-opus-20240229-v1:0
        """
    )
    
    parser.add_argument(
        "-i", "--itokens",
        type=int,
        required=True,
        help="Number of input tokens"
    )
    
    parser.add_argument(
        "-o", "--otokens",
        type=int,
        required=True,
        help="Number of output tokens"
    )
    
    parser.add_argument(
        "-m", "--model",
        type=str,
        default="anthropic.claude-sonnet-4-5-20250929-v1:0",
        help="AWS Bedrock model identifier (default: anthropic.claude-sonnet-4-5-20250929-v1:0)"
    )
    
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List all available models and their pricing"
    )
    
    args = parser.parse_args()
    
    if args.list_models:
        print("\nAvailable AWS Bedrock Models and Pricing:")
        print("=" * 80)
        for model, pricing in AWS_BEDROCK_PRICING.items():
            print(f"\nModel: {model}")
            print(f"  Input:  ${pricing['input']:.6f} per 1K tokens (${pricing['input'] * 1000:.2f} per 1M tokens)")
            print(f"  Output: ${pricing['output']:.6f} per 1K tokens (${pricing['output'] * 1000:.2f} per 1M tokens)")
        print("=" * 80)
        return
    
    # Calculate costs
    input_cost, output_cost, total_cost = calculate_cost(
        args.itokens,
        args.otokens,
        args.model
    )
    
    # Display results
    print("\n" + "=" * 60)
    print("AWS Bedrock Cost Approximation")
    print("=" * 60)
    print(f"Model: {args.model}")
    print(f"\nInput Tokens:  {args.itokens:,}")
    print(f"Output Tokens: {args.otokens:,}")
    print(f"Total Tokens:  {args.itokens + args.otokens:,}")
    print("-" * 60)
    print(f"Input Cost:    {format_cost(input_cost)}")
    print(f"Output Cost:   {format_cost(output_cost)}")
    print(f"Total Cost:    {format_cost(total_cost)}")
    print("=" * 60)
    
    # Show cost per million tokens for reference
    pricing = AWS_BEDROCK_PRICING[args.model]
    print(f"\nPricing (per 1M tokens):")
    print(f"  Input:  ${pricing['input'] * 1000:.2f}")
    print(f"  Output: ${pricing['output'] * 1000:.2f}")
    print()


if __name__ == "__main__":
    main()